import asyncio
from datetime import timedelta
from typing import Dict, List, Optional

from .models import ClientSession, TaskResult, utc_now


class Registry:
    def __init__(self) -> None:
        self._clients: Dict[str, ClientSession] = {}
        self._tasks: Dict[str, TaskResult] = {}
        self._lock = asyncio.Lock()

    async def register(self, client: ClientSession) -> None:
        async with self._lock:
            self._clients[client.client_id] = client

    async def remove_client(self, client_id: Optional[str]) -> None:
        if not client_id:
            return
        async with self._lock:
            self._clients.pop(client_id, None)

    async def list_clients(self) -> List[Dict[str, object]]:
        async with self._lock:
            return [
                {
                    "client_id": c.client_id,
                    "agent_version": c.version,
                    "last_seen": c.last_seen.isoformat(),
                    "system": c.system,
                }
                for c in self._clients.values()
            ]

    async def client_ids(self) -> List[str]:
        async with self._lock:
            return list(self._clients.keys())

    async def get_client(self, client_id: str) -> Optional[ClientSession]:
        async with self._lock:
            return self._clients.get(client_id)

    async def touch(self, client_id: Optional[str]) -> None:
        if not client_id:
            return
        async with self._lock:
            c = self._clients.get(client_id)
            if c:
                c.touch()

    async def create_task(self, task: TaskResult) -> None:
        async with self._lock:
            self._tasks[task.task_id] = task

    async def update_task(self, task_id: str, client_id: str, status: str, output: str) -> None:
        async with self._lock:
            current = self._tasks.get(task_id)
            if current is None:
                self._tasks[task_id] = TaskResult(task_id, client_id, "unknown", status, output)
                return
            self._tasks[task_id] = TaskResult(
                task_id=current.task_id,
                client_id=current.client_id,
                action=current.action,
                status=status,
                output=output,
            )

    async def get_task(self, task_id: str) -> Optional[Dict[str, object]]:
        async with self._lock:
            t = self._tasks.get(task_id)
            if not t:
                return None
            return {
                "task_id": t.task_id,
                "client_id": t.client_id,
                "action": t.action,
                "status": t.status,
                "output": t.output,
                "updated_at": t.updated_at.isoformat(),
            }

    async def inactive_clients(self, timeout_seconds: int) -> List[ClientSession]:
        cutoff = utc_now() - timedelta(seconds=timeout_seconds)
        async with self._lock:
            ids = [cid for cid, c in self._clients.items() if c.last_seen < cutoff]
            stale = [self._clients[cid] for cid in ids]
            for cid in ids:
                self._clients.pop(cid, None)
            return stale
