import json
import logging
import ssl
import threading
import time
from typing import Any, Dict

import websocket

from .config import ClientSettings
from .executor import Executor
from .reconnect import backoff_sleep
from .system_info import (
    get_current_user,
    get_hostname,
    get_primary_ip,
    system_descriptor,
)
from .tty import TTYManager
from .transfer import TransferManager


log = logging.getLogger("dacs.client")


class Agent:
    def __init__(self, settings: ClientSettings) -> None:
        self.settings = settings
        self.executor = Executor()
        self.stop_event = threading.Event()
        self.tty_manager = None
        self.transfer_manager = None

    def run_forever(self) -> None:
        attempt = 0
        while not self.stop_event.is_set():
            duration = self._connect_once()
            if self.stop_event.is_set():
                break

            if duration < 5:
                attempt += 1
            else:
                attempt = 1

            delay = backoff_sleep(
                attempt,
                base=self.settings.reconnect_base_seconds,
                cap=self.settings.reconnect_cap_seconds,
            )
            log.info("reconnect scheduled in %.1fs (attempt=%d)", delay, attempt)

    def _connect_once(self) -> float:
        ws = websocket.WebSocketApp(
            self.settings.server_url,
            on_open=lambda conn: self._on_open(conn),
            on_message=lambda conn, msg: self._on_message(conn, msg),
            on_close=lambda _conn, code, reason: self._on_close(code, reason),
            on_error=lambda _conn, err: self._on_error(err),
        )

        sslopt = None
        if self.settings.server_url.startswith("wss://"):
            sslopt = {"cert_reqs": ssl.CERT_NONE if self.settings.insecure_tls else ssl.CERT_REQUIRED}

        started = time.time()
        ws.run_forever(
            sslopt=sslopt,
            ping_interval=self.settings.ping_interval_seconds,
            ping_timeout=self.settings.ping_timeout_seconds,
        )
        return time.time() - started

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        ws.send(json.dumps(self._register_message()))
        thread = threading.Thread(target=self._heartbeat_loop, args=(ws,), daemon=True)
        thread.start()
        log.info("connected client_id=%s server=%s", self.settings.client_id, self.settings.server_url)

    def _on_close(self, code: Any, reason: Any) -> None:
        code_text = "none" if code is None else str(code)
        reason_text = str(reason).strip() if reason is not None else ""

        # 1001 is commonly used for server-side close/restart signals.
        if code == 1001 and not reason_text:
            reason_text = "server_closed"
        if not reason_text:
            reason_text = "no_reason"

        if code in (1000, 1001):
            log.info("disconnected code=%s reason=%s", code_text, reason_text)
        else:
            log.warning("disconnected code=%s reason=%s", code_text, reason_text)

    def _on_error(self, err: Any) -> None:
        text = str(err)
        if "Connection refused" in text:
            log.error("connection refused by server (is server running on %s?)", self.settings.server_url)
            return
        log.error("socket error: %s", text)

    def _heartbeat_loop(self, ws: websocket.WebSocketApp) -> None:
        while not self.stop_event.is_set():
            time.sleep(self.settings.heartbeat_seconds)
            try:
                ws.send(json.dumps({"type": "ping", "client_id": self.settings.client_id}))
            except Exception:
                return

    def _on_message(self, ws: websocket.WebSocketApp, raw_message: str) -> None:
        try:
            message = json.loads(raw_message)
        except json.JSONDecodeError:
            log.warning("invalid_json")
            return

        mtype = message.get("type")

        if mtype == "tty_start":
            if self.tty_manager:
                self.tty_manager.stop()
            self.tty_manager = TTYManager(ws, self.settings.client_id)
            self.tty_manager.start()
            return
        elif mtype == "tty_input":
            if self.tty_manager:
                self.tty_manager.write(message.get("data", ""))
            return
        elif mtype == "tty_stop":
            if self.tty_manager:
                self.tty_manager.stop()
                self.tty_manager = None
            return
        elif mtype == "upload_start":
            if not self.transfer_manager:
                self.transfer_manager = TransferManager(ws)
            self.transfer_manager.ws = ws
            self.transfer_manager.handle_upload_start(message.get("transfer_id"), message.get("remote_path"))
            return
        elif mtype == "upload_chunk":
            if self.transfer_manager:
                self.transfer_manager.handle_upload_chunk(message.get("transfer_id"), message.get("data"))
            return
        elif mtype == "upload_end":
            if self.transfer_manager:
                self.transfer_manager.handle_upload_end(message.get("transfer_id"))
            return
        elif mtype == "download_start":
            if not self.transfer_manager:
                self.transfer_manager = TransferManager(ws)
            self.transfer_manager.ws = ws
            self.transfer_manager.start_download(message.get("transfer_id"), message.get("remote_path"))
            return

        if mtype != "command":
            return

        task_id = message.get("task_id", "")
        action = message.get("action", "")
        payload = message.get("payload") or {}
        log.info("action received action=%s task_id=%s", action, task_id)

        result = self.executor.run(action, payload)
        log.info(
            "action executed action=%s task_id=%s status=%s",
            action,
            task_id,
            result.get("status"),
        )
        ws.send(
            json.dumps(
                {
                    "type": "result",
                    "client_id": self.settings.client_id,
                    "task_id": task_id,
                    "status": result["status"],
                    "output": result["output"],
                }
            )
        )
        log.info("result sent action=%s task_id=%s", action, task_id)

        if action == "restart_agent" and result["status"] == "success":
            time.sleep(0.5)
            ws.close()

    def _register_message(self) -> Dict[str, Any]:
        system = system_descriptor()
        return {
            "type": "register",
            "client_id": self.settings.client_id,
            "token": self.settings.token,
            "version": self.settings.version,
            "system": {
                "hostname": get_hostname(),
                "ip": get_primary_ip(),
                "os": system.get("os", "unknown"),
                "user": get_current_user(),
            },
        }
