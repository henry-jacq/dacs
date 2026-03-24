import base64
import json
import sys
import uuid
from typing import Any, Dict, Optional, Tuple

from websockets.asyncio.server import ServerConnection

from .logger import console_print
from .models import ALLOWED_ACTIONS, TaskResult, command_message
from .state import active_downloads, registry

async def send_command(client_id: str, action: str, payload: Optional[Dict[str, Any]]) -> Tuple[bool, str]:
    if action not in ALLOWED_ACTIONS:
        return False, "action_not_allowed"

    client = await registry.get_client(client_id)
    if not client:
        return False, "client_not_connected"

    task_id = str(uuid.uuid4())
    await registry.create_task(TaskResult(task_id, client_id, action, "dispatched", ""))

    try:
        await client.websocket.send(json.dumps(command_message(task_id, action, payload or {})))
        console_print(
            "Dispatched action='%s' task_id=%s target=%s"
            % (action, task_id, client_id),
            "INFO",
        )
        return True, task_id
    except Exception as exc:
        await registry.update_task(task_id, client_id, "error", f"dispatch_failed: {exc}")
        return False, "dispatch_failed"

async def handle_client_messages(client_id: str, websocket: ServerConnection) -> None:
    while True:
        raw = await websocket.recv()
        if isinstance(raw, bytes):
            continue
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            continue

        mtype = message.get("type")
        if mtype in ("ping", "pong"):
            await registry.touch(client_id)
            continue

        if mtype == "tty_output":
            data = message.get("data", "")
            sys.stdout.write(data)
            sys.stdout.flush()
            continue

        if mtype == "download_chunk":
            transfer_id = message.get("transfer_id")
            b64_data = message.get("data")
            if transfer_id in active_downloads:
                try:
                    data = base64.b64decode(b64_data)
                    active_downloads[transfer_id].write(data)
                except Exception as e:
                    console_print(f"Error decoding chunk: {e}", "ERROR")
            continue

        if mtype == "download_end":
            transfer_id = message.get("transfer_id")
            f = active_downloads.pop(transfer_id, None)
            if f:
                f.close()
                console_print(f"Download {transfer_id} finished", "INFO")
            continue
            
        if mtype == "download_error":
            transfer_id = message.get("transfer_id")
            err = message.get("error", "Unknown")
            f = active_downloads.pop(transfer_id, None)
            if f:
                f.close()
            console_print(f"Download {transfer_id} failed: {err}", "ERROR")
            continue
            
        if mtype == "upload_ready":
            continue
        if mtype == "upload_error":
            console_print(f"Upload failed: {message.get('error')}", "ERROR")
            continue

        if mtype == "result":
            task_id = message.get("task_id", "")
            status = message.get("status", "unknown")
            output = message.get("output", "")
            await registry.update_task(
                task_id=task_id,
                client_id=client_id,
                status=status,
                output=output,
            )
            await registry.touch(client_id)

            task_info = await registry.get_task(task_id)
            action = task_info.get("action", "unknown") if task_info else "unknown"
            preview = str(output).replace("\n", " ")
            if len(preview) > 120:
                preview = preview[:117] + "..."
            console_print(
                "Result action='%s' task_id=%s client=%s status=%s output=%s"
                % (action, task_id, client_id, status, preview),
                "INFO",
            )
            continue
