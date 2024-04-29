import asyncio
import json
import logging
import uuid
import aiohttp_cors

from pathlib import Path

from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription

from python.datachannel import setup_datachannel
from python.videowriter import VideoRecorder

logging.basicConfig(level=logging.INFO)
logging.getLogger().addHandler(logging.StreamHandler())
logger = logging.getLogger("pc")

async def test(request):
    return web.Response(
        content_type="application/json",
        text="test",
        headers={
            'Access-Control-Allow-Origin': '*'
        }
    )

async def index(request):
    with (Path(__file__).parent / 'client/index.html').open() as f:
        return web.Response(content_type="text/html", text=f.read())


async def javascript(request):
    with (Path(__file__).parent / 'client/js/client.js').open() as f:
        return web.Response(content_type="application/javascript", text=f.read())


class OptionsWrapper:
    def __init__(self, raw_options):
        self.raw_options = raw_options

    @property
    def camera_type(self):
        return self.raw_options.get('camera_type', 'rgb')

    @property
    def width(self):
        return int(self.raw_options.get('cam_width', 500))

    @property
    def height(self):
        return int(self.raw_options.get('cam_height', 374))

    @property
    def nn(self):
        return self.raw_options.get('nn_model', '')

    @property
    def mono_camera_resolution(self):
        return self.raw_options.get('mono_camera_resolution', 'THE_400_P')

    @property
    def median_filter(self):
        return self.raw_options.get('median_filter', 'KERNEL_7x7')
    
    @property
    def subpixel(self):
        return bool(self.raw_options.get('subpixel', ''))

    @property
    def extended_disparity(self):
        return bool(self.raw_options.get('extended_disparity', ''))


async def offer(request):
    params = await request.json()
    options = OptionsWrapper(params.get("options", dict()))
    rtc_offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

    pc = RTCPeerConnection()
    pc_id = "PeerConnection({})".format(uuid.uuid4())
    request.app.pcs.add(pc)

    # handle offer
    await pc.setRemoteDescription(rtc_offer)

    logger.info("Created for {}".format(request.remote))

    setup_datachannel(pc, pc_id, request.app)
    for transceiver in pc.getTransceivers():
        if transceiver.kind == "video":
            # request.app.video_transforms[pc_id] = VideoRecorder(request.app, pc_id, options)
            # pc.addTrack(request.app.video_transforms[pc_id])
            request.app.video_transform = VideoRecorder(request.app, pc_id, options)
            pc.addTrack(request.app.video_transform)


    @pc.on("iceconnectionstatechange")
    async def on_iceconnectionstatechange():
        logger.info("ICE connection state is {}".format(pc.iceConnectionState))
        if pc.iceConnectionState == "failed":
            await pc.close()
            request.app.pcs.discard(pc)

    @pc.on("track")
    def on_track(track):
        logger.info("Track {} received".format(track.kind))

    await pc.setLocalDescription(await pc.createAnswer())

    return web.Response(
        content_type="application/json",
        text=json.dumps({
            "sdp": pc.localDescription.sdp,
            "type": pc.localDescription.type
        }),
    )

async def record_start(request):
    request.app.video_transform.is_recording = True
    return web.Response()

async def record_stop(request):
    request.app.video_transform.is_recording = False
    return web.Response()

async def on_shutdown(application):
    # close peer connections
    coroutines = [pc.close() for pc in application.pcs]
    await asyncio.gather(*coroutines)
    application.pcs.clear()


def init_app(application):
    setattr(application, 'pcs', set())
    setattr(application, 'pcs_datachannels', {})
    setattr(application, 'video_transforms', {})



if __name__ == "__main__":
    app = web.Application()
    init_app(app)
    app.on_shutdown.append(on_shutdown)
    app.router.add_get("/", index)
    app.router.add_get("/client.js", javascript)
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
                allow_credentials=True,
                expose_headers="*",
                allow_headers="*",
            )
    })
    cors.add(app.router.add_get("/test", test))
    cors.add(app.router.add_post("/offer", offer))
    cors.add(app.router.add_get("/record_start", record_start))
    cors.add(app.router.add_get("/record_stop", record_stop))
    web.run_app(app, access_log=None, port=8080)
