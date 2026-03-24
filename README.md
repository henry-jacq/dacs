# DACS

Distributed Agent Control System (Python MVP) using persistent WebSocket connections.

## Components

- `server/`: async WebSocket controller with session console
- `client/`: reconnecting agent with heartbeat and action executor
- `deploy/apache/`: Apache reverse-proxy example for WSS

## Project Layout

```text
server/
  app/
    main.py
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

## Configuration

Server and client both load config in this order:

1. `.env`
2. JSON config
3. environment variables (override files)
4. defaults

## Quick Start

```bash
cd /home/henry/dacs
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

```bash
cp server/config/.env.example server/config/.env
cp server/config/server.example.json server/config/server.json
cp client/config/.env.example client/config/.env
cp client/config/client.example.json client/config/client.json
```

Use the same `DACS_AGENT_TOKEN` value on server and client.
Optional client tuning:
`DACS_WS_PING_INTERVAL_SECONDS`, `DACS_WS_PING_TIMEOUT_SECONDS`,
`DACS_RECONNECT_BASE_SECONDS`, `DACS_RECONNECT_CAP_SECONDS`.

Start server:

```bash
python -m server.app.main
```

Start client(s):

```bash
python -m client.app.main
```

For multiple clients, set unique `DACS_CLIENT_ID` values.

## Server Console Commands

- `help`
- `sessions` or `clients`
- `use <client_id>`
- `run <action_name> [payload_json]`
- `back`
- `send <client_id> <action_name> [payload_json]`
- `broadcast <action_name> [payload_json]`
- `task <task_id>`
- `quit`

Available actions:

- `echo`
- `collect_system`
- `list_processes`
- `list_directory`
- `restart_agent`

Notes:

- `run <action_name>` without payload enters interactive input mode based on action schema.
- `run <action_name> <payload_json>` still works for non-interactive dispatch.

## Apache (WSS)

Use [dacs.conf](/home/henry/dacs/deploy/apache/dacs.conf) and proxy `/ws` to `ws://127.0.0.1:8080/ws`.
