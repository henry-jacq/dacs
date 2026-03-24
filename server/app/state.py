from typing import Any, Dict, Set

from .config import load_settings
from .registry import Registry

settings = load_settings()
registry = Registry()
active_tty_sessions: Set[str] = set()
active_downloads: Dict[str, Any] = {}
