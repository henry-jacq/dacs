import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from .env_loader import load_dotenv


@dataclass(frozen=True)
class ClientSettings:
    server_url: str
    client_id: str
    token: str
    version: str
    heartbeat_seconds: float
    insecure_tls: bool


def _to_bool(value: Any, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return fallback
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _to_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def load_settings() -> ClientSettings:
    base_dir = Path(__file__).resolve().parents[1]
    load_dotenv(Path(os.getenv("DACS_CLIENT_DOTENV", base_dir / "config" / ".env")))

    cfg = _load_json(Path(os.getenv("DACS_CLIENT_CONFIG", base_dir / "config" / "client.json")))

    return ClientSettings(
        server_url=os.getenv("DACS_SERVER_URL", cfg.get("server_url", "ws://127.0.0.1:8080/ws")),
        client_id=os.getenv("DACS_CLIENT_ID", cfg.get("client_id", "node-local")),
        token=os.getenv("DACS_AGENT_TOKEN", cfg.get("token", "change-me-agent-token")),
        version=os.getenv("DACS_AGENT_VERSION", cfg.get("version", "1.0")),
        heartbeat_seconds=_to_float(os.getenv("DACS_HEARTBEAT_SECONDS", cfg.get("heartbeat_seconds", 10.0)), 10.0),
        insecure_tls=_to_bool(os.getenv("DACS_INSECURE_TLS", cfg.get("insecure_tls", False))),
    )
