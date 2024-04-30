import traceback
import numpy as np
import cv2
import av
import depthai as dai
import blobconverter

from aiortc import VideoStreamTrack
from fractions import Fraction

import time
import sys

class VideoTransformTrack(VideoStreamTrack):
    def __init__(self, application, pc_id, options):
        super().__init__()  # don't forget this!
        self.dummy = False
        self.application = application
        self.pc_id = pc_id
        self.options = options
        self.frame = None

    async def get_frame(self):
        raise NotImplementedError()

    async def return_frame(self, frame):
        pts, time_base = await self.next_timestamp()
        new_frame = av.VideoFrame.from_ndarray(frame, format="bgr24")
        new_frame.pts = pts
        new_frame.time_base = time_base
        return new_frame

    async def dummy_recv(self):
        frame = np.zeros((self.options.height, self.options.width, 3), np.uint8)
        y, x = frame.shape[0] / 2, frame.shape[1] / 2
        left, top, right, bottom = int(x - 50), int(y - 30), int(x + 50), int(y + 30)
        cv2.rectangle(frame, (left, top), (right, bottom), (0, 0, 255), cv2.FILLED)
        cv2.putText(frame, "ERROR", (left, int((bottom + top) / 2 + 10)), cv2.FONT_HERSHEY_DUPLEX, 1.0,
                    (255, 255, 255), 1)
        return await self.return_frame(frame)

    async def recv(self):
        if self.dummy:
            return await self.dummy_recv()
        try:
            frame = await self.get_frame()
            return await self.return_frame(frame)
        except:
            print(traceback.format_exc())
            print('Switching to dummy mode...')
            self.dummy = True
            return await self.dummy_recv()


def frameNorm(frame, bbox):
    normVals = np.full(len(bbox), frame.shape[0])
    normVals[::2] = frame.shape[1]
    return (np.clip(np.array(bbox), 0, 1) * normVals).astype(int)


class VideoRecorder(VideoTransformTrack):
    def __init__(self, application, pc_id, options):
        super().__init__(application, pc_id, options)

        self.is_recording = False
        self.frame = np.zeros((self.options.height, self.options.width, 3), np.uint8)
        self.frame[:] = (0, 0, 0)
        self.detections = []

        # ---------- Create pipeline
        self.pipeline = dai.Pipeline()

        # ---------- Define sources and outputs
        self.encoder = self.pipeline.create(dai.node.VideoEncoder)
        self.camRgb = self.pipeline.create(dai.node.ColorCamera)
        # self.detection = self.pipeline.create(dai.node.MobileNetDetectionNetwork)

        self.xoutRgb = self.pipeline.create(dai.node.XLinkOut)
        self.xoutEnc = self.pipeline.create(dai.node.XLinkOut)
        # self.xoutNN = self.pipeline.create(dai.nodeXLinkOut)

        self.xoutRgb.setStreamName("rgb")
        self.xoutEnc.setStreamName("enc")
        # self.xoutNN.setStreamName("nn")

        # ---------- Properties
        self.camRgb.setResolution(dai.ColorCameraProperties.SensorResolution.THE_1080_P)
        # self.camRgb.setResolution(dai.ColorCameraProperties.SensorResolution.THE_4_K)
        self.camRgb.setPreviewSize(self.options.width, self.options.height)
        self.camRgb.setInterleaved(False)
        self.camRgb.setColorOrder(dai.ColorCameraProperties.ColorOrder.RGB)

        # Encoding choices
        # self.encoder.setDefaultProfilePreset(30, dai.VideoEncoderProperties.Profile.H264_MAIN)
        self.encoder.setDefaultProfilePreset(30, dai.VideoEncoderProperties.Profile.MJPEG)
        # self.encoder.setDefaultProfilePreset(30, dai.VideoEncoderProperties.Profile.H265_MAIN)
        # self.encoder.setLossless(True) # Lossless MJPEG, video players usually don't support it

        # NN Detection choices
        # self.detection.setConfidenceThreshold(0.5)
        # self.detection.setBlobPath(nnPath)
        # self.detection.setNumInferenceThreads(2)
        # self.detection.input.setBlocking(False)
        # self.detection.setBlobPath(blobconverter.from_zoo(options.nn, shaves=6))

        # ---------- Linking
        self.camRgb.video.link(self.encoder.input)
        self.camRgb.preview.link(self.xoutRgb.input)
        # self.camRgb.preview.link(self.detection.input)
        self.encoder.bitstream.link(self.xoutEnc.input)
        # self.detection.out.link(self.xoutNN.input)

        self.nn = None

        # ---------- Queues
        self.device = dai.Device(self.pipeline)
        self.qRgb = self.device.getOutputQueue(name="rgb", maxSize=1, blocking=False)
        # self.qDet = self.device.getOutputQueue(name="nn", maxSize=4, blocking=False)
        self.qRgbEnc = self.device.getOutputQueue(name="enc", maxSize=30, blocking=False)

        self.enc_setup = False
        self.device.startPipeline()

        print("VideoRecorder initialized")
        
    async def get_frame(self):
        frame = self.qRgb.tryGet()
        if frame is not None:
            self.frame = frame.getCvFrame()
        
        if self.is_recording:
            if self.enc_setup is not True:
                # Set up for encoding
                self.enc_setup = True
                self.enc_start = time.time()
                self.enc_container = av.open('/media/gary/Samsung USB/video.mp4', 'w')
                # enc_stream = self.enc_container.add_stream("hevc", rate=30)
                # enc_stream = self.enc_container.add_stream("h264", rate=30)
                enc_stream = self.enc_container.add_stream("mjpeg", rate=30)
                enc_stream.time_base = Fraction(1, 1000 * 1000) # Microseconds
                enc_stream.pix_fmt = "yuvj420p"

            while self.qRgbEnc.has():
                data = self.qRgbEnc.get().getData() # np.array
                packet = av.Packet(data) # Create new packet with byte array
            
                # Set frame timestamp
                packet.pts = int((time.time() - self.enc_start) * 1000 * 1000)
                self.enc_container.mux_one(packet) # Mux the Packet into container

        return self.frame

    def stop(self):
        print("VideoRecorder stop...")
        super().stop()

        # Clean up
        self.enc_container.close()
        del self.device
