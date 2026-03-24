# DACS MVP (Python, WebSocket-Only)

Minimal centralized agent control system using persistent WebSocket connections.

- `server/` async WebSocket server + interactive console
- `client/` persistent reconnecting agent
- `deploy/apache/` reverse proxy example for WSS on 443

## Why your 403 happened

The previous FastAPI/ASGI handshake path could reject WebSocket upgrades (403), and the client retried too fast.

This version removes FastAPI/Uvicorn entirely and uses pure `websockets` server, plus reconnect backoff to avoid connection floods.

## Structure

```text
server/
  app/
    main.py        # websocket server + operator console
    config.py
    registry.py
    models.py
    env_loader.py
  config/
    .env.example
    server.example.json

client/
  app/
    main.py
    agent.py
    config.py
    executor.py
    reconnect.py
    env_loader.py
  config/
    .env.example
    client.example.json
```

## Config auto-loading

Both server and client auto-load in this order:

1. `.env` file
2. JSON file
3. process env variables override file values
4. defaults

## Install

```bash
cd /home/henry/dacs
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configure

```bash
cp server/config/.env.example server/config/.env
cp server/config/server.example.json server/config/server.json
cp client/config/.env.example client/config/.env
cp client/config/client.example.json client/config/client.json
```

Set same `DACS_AGENT_TOKEN` in server and client config.

## Run server (with interactive console)

```bash
cd /home/henry/dacs
source .venv/bin/activate
python -m server.app.main
```

Server session console commands:

- `help`
- `sessions` / `clients`
- `use <client_id>`
- `run <action> [payload_json]` (runs on active session)
- `back` (leave active session)
- `send <client_id> <action> [payload_json]`
- `broadcast <action> [payload_json]`
- `task <task_id>`
- `quit`

Allowed actions: `echo`, `collect_system`, `list_processes`, `list_directory`, `restart_agent`

## Run one or more clients

```bash
cd /home/henry/dacs
source .venv/bin/activate
python -m client.app.main
```

Use different `DACS_CLIENT_ID` values for multiple clients.

## Apache WSS

`deploy/apache/dacs.conf` still applies. Route `/ws` to `ws://127.0.0.1:8080/ws`.
