import json
import os
import socket
from pathlib import Path
from typing import Any, Dict

import psutil


class Executor:
    ALLOWED_ACTIONS = {"echo", "collect_system", "list_processes", "list_directory", "restart_agent"}

    def run(self, action: str, payload: Dict[str, Any]) -> Dict[str, str]:
        if action not in self.ALLOWED_ACTIONS:
            return {"status": "error", "output": f"action_not_allowed: {action}"}

        try:
            if action == "echo":
                return {"status": "success", "output": str(payload.get("message", ""))}
            if action == "collect_system":
                return {"status": "success", "output": self._collect_system()}
            if action == "list_processes":
                return {"status": "success", "output": self._list_processes()}
            if action == "list_directory":
                return {"status": "success", "output": self._list_directory(payload)}
            if action == "restart_agent":
                return {"status": "success", "output": "restart_requested"}
        except Exception as exc:
            return {"status": "error", "output": f"execution_error: {exc}"}

        return {"status": "error", "output": "unknown_action"}

    def _collect_system(self) -> str:
        payload = {
            "hostname": socket.gethostname(),
            "platform": os.name,
            "cpu_percent": psutil.cpu_percent(interval=0.2),
            "memory": dict(psutil.virtual_memory()._asdict()),
            "boot_time": psutil.boot_time(),
        }
        return json.dumps(payload)

    def _list_processes(self) -> str:
        data = []
        for proc in psutil.process_iter(attrs=["pid", "name", "username"]):
            info = proc.info
            data.append({"pid": info.get("pid"), "name": info.get("name"), "username": info.get("username")})
        return json.dumps(data[:100])

    def _list_directory(self, payload: Dict[str, Any]) -> str:
        target = Path(payload.get("path", ".")).resolve()
        if not target.exists() or not target.is_dir():
            return f"invalid_directory: {target}"

        items = []
        for child in list(target.iterdir())[:200]:
            stat = child.stat()
            items.append(
                {
                    "name": child.name,
                    "is_dir": child.is_dir(),
                    "size": stat.st_size if child.is_file() else 0,
                    "mtime": stat.st_mtime,
                }
            )
        return json.dumps({"path": str(target), "items": items})
