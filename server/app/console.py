import asyncio
import base64
import json
import os
import shlex
import sys
import uuid
from typing import Any, Dict, Optional

from .handlers import send_command
from .logger import _BOLD, _CYAN, _DIM, _RESET, console_print, set_prompt_state
from .state import active_downloads, active_tty_sessions, registry
from .utils import (
    _build_payload_interactive,
    _parse_json_payload,
    clear_screen,
    format_clients_for_json,
    render_help,
    setup_readline,
)

async def console_loop() -> None:
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
        set_prompt_state(True, prompt)
        line = await asyncio.to_thread(input, prompt)
        set_prompt_state(False, prompt)
        
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
            print(f"\n{_CYAN}[*] Attached to Session: {target}{_RESET}\n")
            continue

        if line == "info":
            if not active_session:
                print("No active session. Use: use <client_id>")
                continue
                
            all_clients = await registry.list_clients()
            c_dict = next((c for c in format_clients_for_json(all_clients) if c["client_id"] == active_session), {})
            if not c_dict:
                print("Session disconnected or unavailable")
                continue
                
            sys_data = c_dict.get("system", {})
            print(f"\n{_CYAN}[*] Configuration: {active_session}{_RESET}")
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
