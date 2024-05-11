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
FPS = 28

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

        self.frame = np.zeros((PREVIEW_HEIGHT, PREVIEW_WIDTH, 3), np.uint8)
        self.frame[:] = (0, 0, 0)

        self.stitcher = None
        self.translateX = 0
        self.translateY = 0

        self.cam1, self.q1 = self.create_cam("18443010915D2D1300")
        self.cam2, self.q2 = self.create_cam("18443010D13E411300")
 
    def create_recorder(self, cam, xout):
        self.enc_container = av.open('video.mp4', mode='w')
        codec = av.CodecContext.create('hevc', 'w')
        self.stream = self.enc_container.add_stream('hevc')
        self.stream.width = 4032
        self.stream.height = 3040
        self.stream.time_base = Fraction(1, 1000 * 1000)

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
        cam.isp.link(imageManip.inputImage)
        
        # Properties
        videoEnc = pipeline.create(dai.node.VideoEncoder)
        videoEnc.setDefaultProfilePreset(FPS, dai.VideoEncoderProperties.Profile.H265_MAIN)
        imageManip.out.link(videoEnc.input)

        videoEnc.bitstream.link(xout.input)

    def create_cam(self, mxid):
        # ---------- Create pipeline
        pipeline = dai.Pipeline()

        # Linking
        xout = pipeline.create(dai.node.XLinkOut)
        xout.setStreamName('bitstream')
        
        # ---------- Define sources and outputs
        cam = pipeline.create(dai.node.ColorCamera)
        cam.setFps(FPS)
        # cam.setResolution(dai.ColorCameraProperties.SensorResolution.THE_12_MP) # (4056, 3040)
        cam.setResolution(dai.ColorCameraProperties.SensorResolution.THE_1080_P)
        cam.setPreviewSize(PREVIEW_WIDTH, PREVIEW_HEIGHT)
        cam.setInterleaved(False)
        cam.setColorOrder(dai.ColorCameraProperties.ColorOrder.BGR)
        cam.setImageOrientation(dai.CameraImageOrientation.ROTATE_180_DEG)
        cam.preview.link(xout.input)

        info = dai.DeviceInfo(mxid)
        device = dai.Device(pipeline, info)

        q = device.getOutputQueue(name="bitstream", maxSize=4, blocking=False)

        print(f"Camera: {device}")
        print(f"Queue: {q}")

        return device, q
        
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
            
        if self.is_recording:
            start_ts = frame1.getTimestampDevice()
            packet = av.Packet(result.getData())

            ts = int((frame1.getTimestampDevice() - start_ts).total_seconds() * 1e6)  # To microsec
            packet.dts = ts + 1  # +1 to avoid zero dts
            packet.pts = ts + 1
            packet.stream = self.stream
            self.enc_container.mux_one(packet)  # Mux the Packet into container

        return result

    def stop(self):
        print("VideoRecorder stop...")
        super().stop()

        # Clean up
        self.enc_container.close()
        self.cam1.close()
        self.cam2.close()
        del self.cam1
        del self.cam2
