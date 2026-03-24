from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ClientSession:
    client_id: str
    websocket: Any
    version: str
    system: Dict[str, str]
    last_seen: datetime = field(default_factory=utc_now)

    def touch(self) -> None:
        self.last_seen = utc_now()


@dataclass
class TaskResult:
    task_id: str
    client_id: str
    action: str
    status: str
    output: str
    updated_at: datetime = field(default_factory=utc_now)


ALLOWED_ACTIONS = {
    "echo",
    "collect_system",
    "list_processes",
    "list_directory",
    "restart_agent",
}


def command_message(task_id: str, action: str, payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "type": "command",
        "task_id": task_id,
        "action": action,
        "payload": payload or {},
    }
