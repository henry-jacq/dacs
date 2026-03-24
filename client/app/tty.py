import json
import os
import subprocess
import threading
import select
import websocket

class TTYManager:
    def __init__(self, ws: websocket.WebSocketApp, client_id: str):
        self.ws = ws
        self.client_id = client_id
        self.process = None
        self.thread = None
        self._stop_event = threading.Event()
        self.fd = None

    def start(self):
        if os.name == "nt":
            cmd = [os.environ.get("COMSPEC", "cmd.exe")]
        else:
            cmd = [os.environ.get("SHELL", "/bin/sh")]

        try:
            import pty
            master_fd, slave_fd = pty.openpty()
            self.process = subprocess.Popen(
                cmd,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                preexec_fn=os.setsid if hasattr(os, "setsid") else None,
            )
            os.close(slave_fd)
            self.fd = master_fd
        except (ImportError, AttributeError, OSError):
            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
            )
            self.fd = self.process.stdout.fileno() if self.process.stdout else None

        self.thread = threading.Thread(target=self._read_loop, daemon=True)
        self.thread.start()

    def write(self, data_str: str):
        if self.process:
            try:
                # Send data to PTY fd if it exists and process stdin is not overridden
                if self.fd is not None and (not self.process.stdin or self.process.stdin.closed):
                    os.write(self.fd, data_str.encode("utf-8"))
                elif self.process.stdin:
                    self.process.stdin.write(data_str.encode("utf-8"))
                    self.process.stdin.flush()
            except Exception:
                pass

    def stop(self):
        self._stop_event.set()
        if self.process:
            try:
                if hasattr(os, "killpg") and hasattr(os, "getsid"):
                    try:
                        os.killpg(os.getsid(self.process.pid), 9)
                    except Exception:
                        self.process.terminate()
                else:
                    self.process.terminate()
            except Exception:
                pass
            self.process = None
        
        if self.fd is not None:
            try:
                os.close(self.fd)
            except Exception:
                pass
            self.fd = None

    def _read_loop(self):
        while not self._stop_event.is_set():
            if self.process is None or self.process.poll() is not None:
                try:
                    self.ws.send(json.dumps({"type": "tty_output", "data": "\r\n[Process Exited]\r\n"}))
                except Exception:
                    pass
                break
            
            if self.fd is None:
                break
                
            try:
                if hasattr(select, "select") and os.name != "nt":
                    r, _, _ = select.select([self.fd], [], [], 1.0)
                    if not r:
                        continue
                
                data = os.read(self.fd, 1024)
                if not data:
                    break
                
                decoded_string = data.decode("utf-8", errors="replace")
                message = {
                    "type": "tty_output",
                    "data": decoded_string
                }
                self.ws.send(json.dumps(message))
            except (OSError, IOError):
                break
            except Exception:
                break