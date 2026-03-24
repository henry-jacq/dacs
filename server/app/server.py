import asyncio
import json
import logging

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from .console import console_loop
from .handlers import handle_client_messages
from .logger import console_print
from .models import ClientSession
from .state import active_tty_sessions, registry, settings
from .utils import _origin_allowed

log = logging.getLogger("dacs.server")

async def ws_handler(websocket: ServerConnection) -> None:
    if websocket.request.path != "/ws":
        await websocket.close(code=1008, reason="invalid_path")
        return

    origin = websocket.request.headers.get("Origin")
    if not _origin_allowed(origin, settings):
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

async def start_server() -> None:
    console_print("Starting server on ws://%s:%s/ws" % (settings.host, settings.port), "INFO")
    async with serve(ws_handler, settings.host, settings.port, ping_interval=20, ping_timeout=20):
        await asyncio.gather(cleanup_loop(), console_loop())
