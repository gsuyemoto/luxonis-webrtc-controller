import json
import urllib.parse
from json import JSONDecodeError
import traceback
import os

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