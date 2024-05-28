import json
import urllib.parse
import traceback
import depthai as dai
import os

from json import JSONDecodeError
from itertools import cycle

# Step size ('W','A','S','D' controls)
STEP_SIZE = 8
# Manual exposure/focus/white-balance set step
EXP_STEP = 500  # us
ISO_STEP = 50
LENS_STEP = 3
WB_STEP = 200

def setup_datachannel(pc, pc_id, app):
    @pc.on("datachannel")
    def on_datachannel(channel):
        app.pcs_datachannels[pc_id] = channel

        @channel.on("message")
        def on_message(message):
            try:
                unquoted = urllib.parse.unquote(message)
                data = json.loads(unquoted)
                if data['type'].upper() == 'PING':
                    channel.send(json.dumps({
                        'type': 'PONG'
                    }))
                elif data['type'].upper() == 'STREAM_CLOSED':
                    channel.send(json.dumps({
                        "type": "CLOSED_SUCCESSFUL",
                        "payload": "Channel is closing..."
                    }))
                    channel.close()
                elif data['type'].upper() == 'STITCH':
                    app.video_transform.is_stitch = True
                    channel.send(json.dumps({
                        "type": "STITCH",
                        "payload": "Stitched images!"
                    }))
                elif data['type'].upper() == 'TOGGLE':
                    toggle = app.video_transform.is_toggle
                    app.video_transform.is_toggle = not toggle
                    channel.send(json.dumps({
                        "type": "TOGGLE",
                        "payload": "Toggle images!"
                    }))
                elif data['type'].upper() == 'RECORD_START':
                    app.video_transform.is_recording = True
                    channel.send(json.dumps({
                        "type": "RECORD_START",
                        "payload": "Start recording video!"
                    }))
                elif data['type'].upper() == 'RECORD_STOP':
                    app.video_transform.is_recording = False
                    channel.send(json.dumps({
                        "type": "RECORD_STOP",
                        "payload": "Stop recording video!"
                    }))
                elif data['type'].upper() == 'SHUTDOWN':
                    if "raspbery" in os.uname: 
                        request.app.shutdown()
                        os.system("shutdown now -h")

                    channel.send(json.dumps({
                        "type": "SHUTDOWN",
                        "payload": "Shutdown Raspi!"
                    }))
                elif data['type'].upper() == 'AWB_MODE':
                    awb_mode = cycle([item for name, item in vars(dai.CameraControl.AutoWhiteBalanceMode).items() if name.isupper()])
                    awb = next(awb_mode)

                    ctrl = dai.CameraControl()
                    ctrl.setAutoWhiteBalanceMode(awb)
                    
                    channel.send(json.dumps({
                        "type": "AWB_MODE",
                        "payload": "AWB Mode changed to: " + awb
                    }))
                elif 'WHITE_BALANCE' in data['type'].upper():
                    if 'MORE' in data['type'].upper():
                        mod = WB_STEP
                    elif 'LESS' in data['type'].upper():
                        mod = -WB_STEP

                    wht_balance = app.video_transform.wbManual + mod 
                    app.video_transform.wbManual = wht_balance
                    
                    ctrl = dai.CameraControl()
                    ctrl.setManualWhiteBalance(wht_balance)

                    print(f"Increase white balance: {wht_balance}")

                    if 'CAM1' in data['type'].upper():
                        app.video_transform.qControl1.send(ctrl)
                    elif 'CAM2' in data['type'].upper():
                        app.video_transform.qControl2.send(ctrl)

                    channel.send(json.dumps({
                        "type": "WHITE_BALANCE",
                        "payload": "White balance set to: " + wht_balance
                    }))
                elif 'EXPOSURE' in data['type'].upper():
                    if 'MORE'  in data['type'].upper():
                        mod = EXP_STEP
                    elif 'LESS'  in data['type'].upper():
                        mod = -EXP_STEP
                    # expTime = clamp(expTime, 1, 33000)
                    # sensIso = clamp(sensIso, 100, 1600)

                    expTime = app.video_transform.expTime + mod 
                    sensIso = app.video_transform.sensIso

                    print("Setting manual exposure, time: ", expTime, "iso: ", sensIso)

                    ctrl = dai.CameraControl()
                    ctrl.setManualExposure(expTime, sensIso)

                    if 'CAM1' in data['type'].upper():
                        app.video_transform.qControl1.send(ctrl)
                    elif 'CAM2' in data['type'].upper():
                        app.video_transform.qControl2.send(ctrl)

                    channel.send(json.dumps({
                        "type": "EXPOSURE",
                        "payload": "Set exposure to: " + expTime
                    }))
                else:
                    channel.send(json.dumps({
                        "type": "BAD_REQUEST",
                        "payload": {
                            "message": "Unknown action type " + data['type'],
                            "received": message,
                        }
                    }))
            except (JSONDecodeError, TypeError) as e:
                channel.send(json.dumps({
                    "type": "BAD_REQUEST",
                    "payload": {
                        "message": "Data passed to API is invalid",
                        "received": message,
                        "error": str(e),
                    }
                }))
            except Exception as e:
                traceback.print_exc()
                channel.send(json.dumps({
                    "type": "SERVER_ERROR",
                    "payload": {
                        "message": "Something's wrong on the server side",
                        "error": str(e),
                    }
                }))
