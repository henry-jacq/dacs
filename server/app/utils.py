import json
import os
import subprocess
from datetime import datetime
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from .logger import _BOLD, _COLOR, _RESET, console_print
from .models import ALLOWED_ACTIONS
from .schemas import ACTION_SCHEMAS

def setup_readline() -> None:
    try:
        import atexit
        import readline
        history_path = os.path.expanduser("~/.dacs_history")
        try:
            readline.read_history_file(history_path)
        except Exception:
            pass
        readline.parse_and_bind("tab: complete")
        readline.parse_and_bind("set editing-mode emacs")
        readline.set_history_length(500)
        atexit.register(readline.write_history_file, history_path)
    except Exception:
        pass

def render_help(in_session: bool = False) -> str:
    available_actions = ", ".join(sorted(ALLOWED_ACTIONS))
    if in_session:
        return "\n".join([
            f"{_BOLD}DACS Interactive Session{_RESET}",
            "----------------------------------------",
            "Commands:",
            "  help              - Show this help menu",
            "  info              - Show active session configuration",
            "  run               - Execute action (usage: run <action_name> [json])",
            "  upload            - Send file to client (usage: upload <server_path> <client_path>)",
            "  download          - Get file from client (usage: download <client_path> [server_path])",
            "  tty               - Enter raw interactive shell",
            "  back              - Exit active session",
            "  clear | cls       - Clear the console screen",
            "",
            "Available actions:",
            f"  {available_actions}",
            "----------------------------------------",
        ])
    return "\n".join([
        f"{_BOLD}DACS Global Console{_RESET}",
        "----------------------------------------",
        "Commands:",
        "  help              - Show this help menu",
        "  sessions|clients  - List all connected clients",
        "  use               - Enter interactive session (usage: use <client_id>)",
        "  tty               - Enter raw interactive shell (usage: tty <client_id>)",
        "  task              - View task result details (usage: task <task_id>)",
        "  clear | cls       - Clear the console screen",
        "  quit              - Stop the server",
        "----------------------------------------",
    ])

def clear_screen() -> None:
    cmd = ["cls"] if os.name == "nt" else ["clear"]
    try:
        subprocess.run(cmd, check=False)
        return
    except Exception:
        pass
    if _COLOR:
        print("\033[2J\033[H", end="", flush=True)
    else:
        print("\n" * 60)

def _pretty_last_seen(iso_timestamp: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_timestamp)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return iso_timestamp

def format_clients_for_json(clients: Any) -> Any:
    out = []
    for client in clients:
        item = dict(client)
        item["last_seen"] = _pretty_last_seen(str(item.get("last_seen", "")))
        out.append(item)
    return out

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

def resolve_upload_path(raw_path: str) -> str:
    expanded = os.path.expanduser(os.path.expandvars(raw_path))
    if os.path.isabs(expanded):
        return expanded
    transfer_dir = os.path.join(os.getcwd(), 'transfers')
    os.makedirs(transfer_dir, exist_ok=True)
    cand1 = os.path.join(transfer_dir, expanded)
    cand2 = os.path.join(os.getcwd(), expanded)
    return cand1 if os.path.exists(cand1) else cand2

def resolve_download_path(raw_path: str) -> str:
    expanded = os.path.expanduser(os.path.expandvars(raw_path))
    if os.path.isabs(expanded):
        return expanded
    transfer_dir = os.path.join(os.getcwd(), 'transfers')
    os.makedirs(transfer_dir, exist_ok=True)
    return os.path.join(os.getcwd(), expanded) if raw_path.startswith("transfers") else os.path.join(transfer_dir, expanded)

def _origin_allowed(origin: Optional[str], settings: Any) -> bool:
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

    LOCAL_HOST_ALIASES = {"localhost", "127.0.0.1", "::1"}
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
