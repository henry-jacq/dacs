import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from .env_loader import load_dotenv


@dataclass(frozen=True)
class ServerSettings:
    host: str
    port: int
    agent_token: str
    inactive_timeout_seconds: int
    cleanup_interval_seconds: int
    allowed_origins: List[str]


def _as_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _parse_origins(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def load_settings() -> ServerSettings:
    base_dir = Path(__file__).resolve().parents[1]
    dotenv_path = Path(os.getenv("DACS_SERVER_DOTENV", base_dir / "config" / ".env"))
    load_dotenv(dotenv_path)

    json_path = Path(os.getenv("DACS_SERVER_CONFIG", base_dir / "config" / "server.json"))
    cfg = _load_json(json_path)

    host = os.getenv("DACS_SERVER_HOST", cfg.get("host", "0.0.0.0"))
    port = _as_int(os.getenv("DACS_SERVER_PORT", cfg.get("port", 8080)), 8080)
    agent_token = os.getenv("DACS_AGENT_TOKEN", cfg.get("agent_token", "change-me-agent-token"))
    inactive_timeout_seconds = _as_int(
        os.getenv("DACS_INACTIVE_TIMEOUT_SECONDS", cfg.get("inactive_timeout_seconds", 45)),
        45,
    )
    cleanup_interval_seconds = _as_int(
        os.getenv("DACS_CLEANUP_INTERVAL_SECONDS", cfg.get("cleanup_interval_seconds", 10)),
        10,
    )
    allowed_origins = _parse_origins(
        os.getenv("DACS_ALLOWED_ORIGINS", cfg.get("allowed_origins", []))
    )

    return ServerSettings(
        host=host,
        port=port,
        agent_token=agent_token,
        inactive_timeout_seconds=inactive_timeout_seconds,
        cleanup_interval_seconds=cleanup_interval_seconds,
        allowed_origins=allowed_origins,
    )
