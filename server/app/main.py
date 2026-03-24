import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from .config import load_settings
from .models import ALLOWED_ACTIONS, ClientSession, TaskResult, command_message
from .registry import Registry


logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("dacs.server")
settings = load_settings()
registry = Registry()
LOCAL_HOST_ALIASES = {"localhost", "127.0.0.1", "::1"}
logging.getLogger("websockets.server").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)

_console_lock = threading.Lock()
_prompt_visible = False
_active_prompt = "dacs> "
_COLOR = sys.stdout.isatty()

_RESET = "\033[0m" if _COLOR else ""
_DIM = "\033[2m" if _COLOR else ""
_BOLD = "\033[1m" if _COLOR else ""
_GREEN = "\033[32m" if _COLOR else ""
_YELLOW = "\033[33m" if _COLOR else ""
_RED = "\033[31m" if _COLOR else ""
_CYAN = "\033[36m" if _COLOR else ""

ACTION_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "echo": {
        "description": "Return a text message",
        "fields": [
            {"name": "message", "prompt": "Message", "required": True},
        ],
    },
    "collect_system": {
        "description": "Collect host/system telemetry",
        "fields": [],
    },
    "list_processes": {
        "description": "List running processes",
        "fields": [],
    },
    "list_directory": {
        "description": "List files in a directory",
        "fields": [
            {"name": "path", "prompt": "Directory path", "required": False, "default": "."},
        ],
    },
    "restart_agent": {
        "description": "Restart the remote agent",
        "fields": [],
    },
}


def setup_readline() -> None:
    """Enable arrow-key history navigation when readline is available."""
    try:
        import atexit
        import readline

        history_path = os.path.expanduser("~/.dacs_history")
        try:
            readline.read_history_file(history_path)
        except FileNotFoundError:
            pass

        readline.parse_and_bind("tab: complete")
        readline.parse_and_bind("set editing-mode emacs")
        readline.set_history_length(500)
        atexit.register(readline.write_history_file, history_path)
    except Exception:
        # Non-fatal: console still works without readline.
        pass


def _now_str() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S")


def _level_style(level: str) -> str:
    normalized = level.upper()
    if normalized == "INFO":
        return f"{_GREEN}{normalized:<5}{_RESET}"
    if normalized in ("WARN", "WARNING"):
        return f"{_YELLOW}{normalized:<5}{_RESET}"
    if normalized in ("ERR", "ERROR"):
        return f"{_RED}{normalized:<5}{_RESET}"
    return f"{_CYAN}{normalized:<5}{_RESET}"


def console_print(message: str, level: str = "INFO") -> None:
    global _prompt_visible
    with _console_lock:
        if _prompt_visible:
            # Move to start of current line and clear it before printing async event.
            if _COLOR:
                sys.stdout.write("\r\033[2K")
            else:
                sys.stdout.write("\r")
        ts = f"{_DIM}{_now_str()}{_RESET}"
        sev = _level_style(level)
        sys.stdout.write(f"{ts} | {sev} | {message}\n")
        if _prompt_visible:
            sys.stdout.write(_active_prompt)
        sys.stdout.flush()


def render_help() -> str:
    available_actions = ", ".join(sorted(ALLOWED_ACTIONS))
    lines = [
        f"{_BOLD}DACS Session Console{_RESET}",
        "----------------------------------------",
        "General:",
        "  help",
        "  sessions | clients",
        "  task <task_id>",
        "  clear | cls",
        "  quit",
        "",
        "Session workflow:",
        "  use <client_id>",
        "  run <action_name> [payload_json]",
        "  back",
        "",
        "Direct dispatch:",
        "  send <client_id> <action_name> [payload_json]",
        "  broadcast <action_name> [payload_json]",
        "",
        "Available actions:",
        f"  {available_actions}",
        "----------------------------------------",
    ]
    return "\n".join(lines)


def clear_screen() -> None:
    """Clear terminal screen across common shells/OSes."""
    cmd = ["cls"] if os.name == "nt" else ["clear"]
    try:
        subprocess.run(cmd, check=False)
        return
    except Exception:
        pass

    # Fallback for minimal terminals
    if _COLOR:
        print("\033[2J\033[H", end="", flush=True)
    else:
        print("\n" * 60)


def _pretty_last_seen(iso_timestamp: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_timestamp)
        local_dt = dt.astimezone()
        return local_dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return iso_timestamp


def format_clients_for_json(clients: Any) -> Any:
    out = []
    for client in clients:
        item = dict(client)
        item["last_seen"] = _pretty_last_seen(str(item.get("last_seen", "")))
        out.append(item)
    return out


def _origin_allowed(origin: Optional[str]) -> bool:
    if not settings.allowed_origins:
        return True
    if not origin:
        return True
    if "*" in settings.allowed_origins:
        return True

    normalized_origin = origin.rstrip("/")
    normalized_allowed = [item.rstrip("/") for item in settings.allowed_origins]
    if normalized_origin in normalized_allowed:
        return True

    origin_host = urlparse(normalized_origin).hostname
    if not origin_host:
        return False

    for allowed in normalized_allowed:
        parsed = urlparse(allowed if "://" in allowed else f"http://{allowed}")
        allowed_host = parsed.hostname
        if not allowed_host:
            continue
        if allowed_host == origin_host:
            return True
        if allowed_host in LOCAL_HOST_ALIASES and origin_host in LOCAL_HOST_ALIASES:
            return True

    return False


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


async def broadcast(action: str, payload: Optional[Dict[str, Any]]) -> None:
    client_ids = await registry.client_ids()
    if not client_ids:
        print("No clients connected")
        return

    for cid in client_ids:
        ok, info = await send_command(cid, action, payload)
        if ok:
            print(f"[{cid}] task_id={info}")
        else:
            print(f"[{cid}] error={info}")


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


async def ws_handler(websocket: ServerConnection) -> None:
    if websocket.request.path != "/ws":
        await websocket.close(code=1008, reason="invalid_path")
        return

    origin = websocket.request.headers.get("Origin")
    if not _origin_allowed(origin):
        console_print(
            "Rejected origin '%s' (allowed=%s)"
            % (origin, settings.allowed_origins),
            "WARN",
        )
        await websocket.close(code=1008, reason="origin_not_allowed")
        return

    client_id = None

    try:
        register_raw = await asyncio.wait_for(websocket.recv(), timeout=10)
        if isinstance(register_raw, bytes):
            await websocket.close(code=1008, reason="invalid_register")
            return

        register = json.loads(register_raw)
        if register.get("type") != "register":
            await websocket.close(code=1008, reason="missing_register")
            return

        if register.get("token") != settings.agent_token:
            await websocket.close(code=1008, reason="auth_failed")
            return

        requested_client_id = register.get("client_id")
        if not requested_client_id:
            await websocket.close(code=1008, reason="missing_client_id")
            return

        existing_ids = set(await registry.client_ids())
        client_id = requested_client_id
        if client_id in existing_ids:
            counter = 2
            while f"{requested_client_id}#{counter}" in existing_ids:
                counter += 1
            client_id = f"{requested_client_id}#{counter}"
            console_print(
                "Duplicate client_id '%s' detected, assigned '%s'"
                % (requested_client_id, client_id),
                "WARN",
            )

        session = ClientSession(
            client_id=client_id,
            websocket=websocket,
            version=register.get("version", "unknown"),
            system=register.get("system", {}),
        )
        await registry.register(session)
        console_print("Client connected: %s" % client_id, "INFO")

        await handle_client_messages(client_id, websocket)

    except ConnectionClosed:
        pass
    except Exception as exc:
        log.exception("Connection error (%s): %s", client_id, exc)
    finally:
        await registry.remove_client(client_id)
        if client_id:
            console_print("Client disconnected: %s" % client_id, "INFO")


async def cleanup_loop() -> None:
    while True:
        await asyncio.sleep(settings.cleanup_interval_seconds)
        stale = await registry.inactive_clients(settings.inactive_timeout_seconds)
        for c in stale:
            console_print("Removing inactive client %s" % c.client_id, "WARN")
            try:
                await c.websocket.close(code=1001, reason="inactive")
            except Exception:
                pass


def _parse_json_payload(raw: str) -> Dict[str, Any]:
    if not raw:
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("payload must be a JSON object")
    return parsed


def _build_payload_interactive(action: str) -> Optional[Dict[str, Any]]:
    schema = ACTION_SCHEMAS.get(action)
    if not schema:
        console_print(f"Unknown action '{action}'. Type 'help' to see available actions.", "WARN")
        return None

    fields = schema.get("fields", [])
    if not fields:
        return {}

    payload: Dict[str, Any] = {}
    print(f"Action: {action}")
    for field in fields:
        name = field.get("name")
        prompt = field.get("prompt", name)
        required = bool(field.get("required", False))
        default = field.get("default")

        suffix = " (required)" if required else ""
        default_hint = f" [default: {default}]" if default is not None else ""
        value = input(f"  {prompt}{suffix}{default_hint}: ").strip()

        if not value:
            if default is not None:
                payload[name] = default
                continue
            if required:
                print(f"  Field '{name}' is required.")
                return None
            continue

        payload[name] = value

    return payload


async def console_loop() -> None:
    global _prompt_visible, _active_prompt
    setup_readline()
    help_text = render_help()
    print(help_text)

    active_session: Optional[str] = None

    while True:
        prompt = f"dacs({active_session})> " if active_session else "dacs> "
        _active_prompt = prompt
        _prompt_visible = True
        line = await asyncio.to_thread(input, prompt)
        _prompt_visible = False
        line = line.strip()
        if not line:
            continue

        if line == "help":
            print(help_text)
            continue

        if line in ("clients", "sessions"):
            clients = await registry.list_clients()
            print(json.dumps(format_clients_for_json(clients), indent=2))
            continue

        if line in ("clear", "cls"):
            clear_screen()
            print(render_help())
            continue

        if line == "back":
            active_session = None
            continue

        if line == "quit":
            raise KeyboardInterrupt

        if line.startswith("use "):
            target = line.split(" ", 1)[1].strip()
            if not target:
                print("Usage: use <client_id>")
                continue
            if not await registry.get_client(target):
                print("Session not found")
                continue
            active_session = target
            continue

        if line.startswith("run "):
            if not active_session:
                print("No active session. Use: use <client_id>")
                continue
            parts = line.split(" ", 2)
            if len(parts) < 2:
                print("Usage: run <action_name> [payload_json]")
                continue
            action = parts[1]
            payload: Optional[Dict[str, Any]] = None
            if len(parts) == 3:
                try:
                    payload = _parse_json_payload(parts[2])
                except Exception as exc:
                    print(f"Invalid payload: {exc}")
                    continue
            else:
                payload = await asyncio.to_thread(_build_payload_interactive, action)
                if payload is None:
                    continue
            ok, info = await send_command(active_session, action, payload)
            print(f"task_id={info}" if ok else f"error={info}")
            continue

        if line.startswith("send "):
            parts = line.split(" ", 3)
            if len(parts) < 3:
                print("Usage: send <client_id> <action_name> [payload_json]")
                continue
            client_id = parts[1]
            action = parts[2]
            payload = {}
            if len(parts) == 4:
                try:
                    payload = _parse_json_payload(parts[3])
                except Exception as exc:
                    print(f"Invalid payload: {exc}")
                    continue
            ok, info = await send_command(client_id, action, payload)
            print(f"task_id={info}" if ok else f"error={info}")
            continue

        if line.startswith("broadcast "):
            parts = line.split(" ", 2)
            if len(parts) < 2:
                print("Usage: broadcast <action_name> [payload_json]")
                continue
            action = parts[1]
            payload = {}
            if len(parts) == 3:
                try:
                    payload = _parse_json_payload(parts[2])
                except Exception as exc:
                    print(f"Invalid payload: {exc}")
                    continue
            await broadcast(action, payload)
            continue

        if line.startswith("task "):
            task_id = line.split(" ", 1)[1].strip()
            result = await registry.get_task(task_id)
            if not result:
                print("Task not found")
            else:
                print(json.dumps(result, indent=2))
            continue

        print("Unknown command. Type 'help'.")


async def main() -> None:
    console_print("Starting server on ws://%s:%s/ws" % (settings.host, settings.port), "INFO")
    async with serve(ws_handler, settings.host, settings.port, ping_interval=20, ping_timeout=20):
        await asyncio.gather(cleanup_loop(), console_loop())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console_print("Server stopped", "INFO")
