import traceback
import numpy as np
import cv2
import av
import depthai as dai
import blobconverter

from aiortc import VideoStreamTrack
from fractions import Fraction
from python.stitching import Stitcher

import time
import sys

PREVIEW_WIDTH = 600
PREVIEW_HEIGHT = 400
# FPS = 30
FPS = 28
# FPS = 20

class VideoTransformTrack(VideoStreamTrack):
    def __init__(self, application, pc_id):
        super().__init__()  # don't forget this!
        self.dummy = False
        self.application = application
        self.pc_id = pc_id
        self.frame = None

    # Has to receive a frame with data, will need to block if frame with data hasn't been received yet
    async def recv(self):
        frame = await self.get_frame()

        # print(f"Frame size: {frame.shape}")

        pts, time_base = await self.next_timestamp()
        new_frame = av.VideoFrame.from_ndarray(frame, format="bgr24")
        new_frame.pts = pts
        new_frame.time_base = time_base
        return new_frame

class VideoRecorder(VideoTransformTrack):
    def __init__(self, application, pc_id):
        super().__init__(application, pc_id)

        self.is_recording = False
        self.is_stitch = False
        self.is_toggle = True

        # Camera configurations
        self.wbManual = 4000
        self.expTime = 20000
        self.sensIso = 800    

        self.frame = np.zeros((PREVIEW_HEIGHT, PREVIEW_WIDTH, 3), np.uint8)
        self.frame[:] = (0, 0, 0)

        self.stitcher = None
        self.translateX = 0
        self.translateY = 0
        self.start_ts = None

        self.cam1, self.q1, self.qControl1, self.recorder1, self.qEncoded1, self.encStream1 = self.create_cam("18443010915D2D1300", "cam1")
        self.cam2, self.q2, self.qControl2, self.recorder2, self.qEncoded2, self.encStream2 = self.create_cam("18443010D13E411300", "cam2")

    def create_cam(self, mxid, name):
        # ---------- Create pipeline
        pipeline = dai.Pipeline()
        
        # ---------- Define sources and outputs
        cam = pipeline.create(dai.node.ColorCamera)
        cam.setFps(FPS)
        cam.setResolution(dai.ColorCameraProperties.SensorResolution.THE_4_K)
        # cam.setResolution(dai.ColorCameraProperties.SensorResolution.THE_12_MP) # (4056, 3040)
        # cam.setResolution(dai.ColorCameraProperties.SensorResolution.THE_1080_P)
        cam.setPreviewSize(PREVIEW_WIDTH, PREVIEW_HEIGHT)
        cam.setInterleaved(False)
        cam.setColorOrder(dai.ColorCameraProperties.ColorOrder.BGR)
        cam.setImageOrientation(dai.CameraImageOrientation.ROTATE_180_DEG)

        # Set up recording
        recorder = av.open('/media/gary/usb_drive/' + name + '.mp4', mode='w')
        codec = av.CodecContext.create('mjpeg', 'w')
        # codec = av.CodecContext.create('hevc', 'w')
        # stream = recorder.add_stream('hevc')
        # stream = recorder.add_stream('h264')
        stream = recorder.add_stream('mjpeg')
        stream.width = 4032
        stream.height = 3040
        stream.time_base = Fraction(1, 1000 * 1000)
        stream.pix_fmt = "yuvj420p" # only for MJPEG
        
        """
        We have to use ImageManip, as ColorCamera.video can output up to 4K.
        Workaround is to use ColorCamera.isp, and convert it to NV12
        """
        imageManip = pipeline.create(dai.node.ImageManip)
        # YUV420 -> NV12 (required by VideoEncoder)
        imageManip.initialConfig.setFrameType(dai.RawImgFrame.Type.NV12)
        # Width must be multiple of 32, height multiple of 8 for H26x encoder
        imageManip.initialConfig.setResize(4032, 3040)
        imageManip.setMaxOutputFrameSize(18495360)
        
        # Encoder
        videoEnc = pipeline.create(dai.node.VideoEncoder)
        videoEnc.setDefaultProfilePreset(FPS, dai.VideoEncoderProperties.Profile.MJPEG)
        # Higher quality would produce huge files
        videoEnc.setQuality(60)
        # videoEnc.setDefaultProfilePreset(FPS, dai.VideoEncoderProperties.Profile.H265_MAIN)

        """
        LINKING -- MAKE SURE THIS PART IS CORRECT! FAILURE IS HARD TO DETECT
        """
        outPreview = pipeline.create(dai.node.XLinkOut)
        outEncoded = pipeline.create(dai.node.XLinkOut)

        outPreview.setStreamName('preview')
        outEncoded.setStreamName('encoded')
        
        print(f"Preview: {outPreview}")
        print(f"Encoded: {outEncoded}")

        # Preview stream
        cam.preview.link(outPreview.input)
        # Encoded stream
        # CAM ISP --> IMAGE MANIPULATOR --> VIDEO ENCODER --> XLINKOUT (HOST)
        # cam.isp.link(imageManip.inputImage)
        # imageManip.out.link(videoEnc.input)
        cam.video.link(videoEnc.input)
        videoEnc.bitstream.link(outEncoded.input)
        
        """
        CONTROL CAMERA CONFIGURATION SETTINGS (EXPOSURE, WHITE BALANCE, ETC.)
        """
        controlIn = pipeline.create(dai.node.XLinkIn)
        configIn = pipeline.create(dai.node.XLinkIn)

        controlIn.setStreamName('control')
        configIn.setStreamName('config')

        controlIn.out.link(cam.inputControl)
        configIn.out.link(cam.inputConfig)

        """
        GET QUEUES AFTER CREATING DEVICE OBJECT
        """
        # Get device by ID
        info = dai.DeviceInfo(mxid)
        device = dai.Device(pipeline, info)

        # Output queue
        qOutPreview = device.getOutputQueue(name="preview", maxSize=4, blocking=False)
        qOutEncoded = device.getOutputQueue(name="encoded", maxSize=30, blocking=False)

        controlQueue = device.getInputQueue('control')
        configQueue = device.getInputQueue('config')

        print(f"Camera: {device}")
        print(f"Queue: {qOutPreview}")
        print(f"Recorder: {recorder}")
        print(f"Queue Encoded: {qOutEncoded}")
        print(f"Stream: {stream}")

        return device, qOutPreview, controlQueue, recorder, qOutEncoded, stream
        
    async def get_frame(self):
        if self.is_toggle:
            frame1 = self.q1.get()
            frame2 = self.q2.get()
        else:
            frame1 = self.q2.get()
            frame2 = self.q1.get()

        img1 = frame1.getCvFrame()
        img2 = frame2.getCvFrame()

        if img1 is None or img2 is None:
            print("Empty image after frame to opencv conversion!!!!")
            return None

        M = np.float32([
            [1, 0, self.translateX],
            [0, 1, self.translateY]
        ])

        if self.translateX != 0 or self.translateY != 0:
            img2 = cv2.warpAffine(img2, M, (WIDTH, HEIGHT))
        
        if self.is_stitch:
            self.stitcher = Stitcher([img1, img2])
            self.is_stitch = False

        if self.stitcher is not None:
            result = self.stitcher.warp([img1, img2])
        else:
            result = np.concatenate((img1, img2), axis=1)        
            
        if self.is_recording and self.qEncoded1.has() and self.qEncoded2.has():
            print("Recording...")
            if self.is_toggle:
                encoded1 = self.qEncoded1.tryGet()
                encoded2 = self.qEncoded2.tryGet()
            else:
                encoded1 = self.qEncoded2.tryGet()
                encoded2 = self.qEncoded1.tryGet()
            
            if self.start_ts is None:
                self.start_ts = encoded1.getTimestampDevice()

            ts = int((encoded1.getTimestampDevice() - self.start_ts).total_seconds() * 1e6)  # To microsec
            packet1 = av.Packet(encoded1.getData())
            packet1.dts = ts + 1  # +1 to avoid zero dts
            packet1.pts = ts + 1
            packet1.stream = self.encStream1
            self.recorder1.mux_one(packet1)  # Mux the Packet into container

            packet2 = av.Packet(encoded2.getData())
            packet2.dts = ts + 1  # +1 to avoid zero dts
            packet2.pts = ts + 1
            packet2.stream = self.encStream2
            self.recorder2.mux_one(packet2)  # Mux the Packet into container

        return result

    def stop(self):
        print("VideoRecorder stop...")
        super().stop()

        # Clean up
        self.recorder1.close()
        self.recorder2.close()
        self.cam1.close()
        self.cam2.close()
        del self.cam1
        del self.cam2
