import sys
import threading
from datetime import datetime, timezone

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

def set_prompt_state(visible: bool, prompt: str = "dacs> ") -> None:
    global _prompt_visible, _active_prompt
    with _console_lock:
        _prompt_visible = visible
        _active_prompt = prompt

def console_print(message: str, level: str = "INFO") -> None:
    global _prompt_visible
    with _console_lock:
        if _prompt_visible:
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
