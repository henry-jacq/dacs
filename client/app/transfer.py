import base64
import json
import logging
import os
import threading
import uuid
import websocket

log = logging.getLogger("dacs.client")

class TransferManager:
    def __init__(self, ws: websocket.WebSocketApp):
        self.ws = ws
        self.active_uploads = {}
        self.active_downloads = {}

    def handle_upload_start(self, transfer_id: str, remote_path: str):
        try:
            expanded_path = os.path.expanduser(os.path.expandvars(remote_path))
            abs_path = os.path.abspath(expanded_path)
            dir_name = os.path.dirname(abs_path)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)
            log.info("Starting upload chunk stream to %s", abs_path)
            f = open(abs_path, "wb")
            self.active_uploads[transfer_id] = f
            self.ws.send(json.dumps({"type": "upload_ready", "transfer_id": transfer_id}))
        except Exception as e:
            log.error("Failed to open file for upload: %s", e)
            self.ws.send(json.dumps({"type": "upload_error", "transfer_id": transfer_id, "error": str(e)}))

    def handle_upload_chunk(self, transfer_id: str, b64_data: str):
        f = self.active_uploads.get(transfer_id)
        if f:
            try:
                data = base64.b64decode(b64_data)
                f.write(data)
            except Exception as e:
                log.error("Failed to write chunk: %s", e)

    def handle_upload_end(self, transfer_id: str):
        f = self.active_uploads.pop(transfer_id, None)
        if f:
            f.close()
            log.info("Upload to %s completed", f.name)

    def start_download(self, transfer_id: str, remote_path: str):
        def _read_loop():
            try:
                expanded_path = os.path.expanduser(os.path.expandvars(remote_path))
                with open(expanded_path, "rb") as f:
                    log.info("Starting download chunk stream from %s", expanded_path)
                    while True:
                        chunk = f.read(512 * 1024)  # 512KB chunks
                        if not chunk:
                            break
                        b64_data = base64.b64encode(chunk).decode("utf-8")
                        self.ws.send(json.dumps({
                            "type": "download_chunk",
                            "transfer_id": transfer_id,
                            "data": b64_data
                        }))
                self.ws.send(json.dumps({"type": "download_end", "transfer_id": transfer_id}))
                log.info("Download from %s completed", remote_path)
            except Exception as e:
                log.error("Failed to read file for download: %s", e)
                try:
                    self.ws.send(json.dumps({"type": "download_error", "transfer_id": transfer_id, "error": str(e)}))
                except Exception:
                    pass

        t = threading.Thread(target=_read_loop, daemon=True)
        t.start()
