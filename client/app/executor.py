import json
import os
import platform
from pathlib import Path
from typing import Any, Dict

import psutil

from .system_info import (
    get_current_user,
    get_hostname,
    get_primary_ip,
    system_descriptor,
)


class Executor:
    ALLOWED_ACTIONS = {"restart_agent"}

    def run(self, action: str, payload: Dict[str, Any]) -> Dict[str, str]:
        if action not in self.ALLOWED_ACTIONS:
            return {"status": "error", "output": f"action_not_allowed: {action}"}

        try:
            if action == "restart_agent":
                return {"status": "success", "output": "restart_requested"}
        except Exception as exc:
            return {"status": "error", "output": f"execution_error: {exc}"}

        return {"status": "error", "output": "unknown_action"}

    def _collect_system(self) -> str:
        descriptor = system_descriptor()
        payload = {
            "hostname": get_hostname(),
            "ip": get_primary_ip(),
            "user": get_current_user(),
            "platform": descriptor.get("platform", platform.platform()),
            "os": descriptor.get("os", os.name),
            "release": descriptor.get("release", ""),
            "machine": descriptor.get("machine", ""),
            "cpu_percent": psutil.cpu_percent(interval=0.2),
            "memory": dict(psutil.virtual_memory()._asdict()),
            "boot_time": psutil.boot_time(),
        }
        return json.dumps(payload, ensure_ascii=True)

    def _list_processes(self) -> str:
        data = []
        for proc in psutil.process_iter(attrs=["pid", "name", "username"]):
            try:
                info = proc.info
                data.append(
                    {
                        "pid": info.get("pid"),
                        "name": info.get("name"),
                        "username": info.get("username"),
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        return json.dumps(data[:100], ensure_ascii=True)

    def _list_directory(self, payload: Dict[str, Any]) -> str:
        raw_path = str(payload.get("path", "."))
        target = Path(os.path.expandvars(os.path.expanduser(raw_path))).resolve()
        if not target.exists() or not target.is_dir():
            return f"invalid_directory: {target}"

        items = []
        for child in list(target.iterdir())[:200]:
            try:
                stat = child.stat()
                items.append(
                    {
                        "name": child.name,
                        "is_dir": child.is_dir(),
                        "size": stat.st_size if child.is_file() else 0,
                        "mtime": stat.st_mtime,
                    }
                )
            except (PermissionError, FileNotFoundError):
                continue
        return json.dumps({"path": str(target), "items": items}, ensure_ascii=True)
