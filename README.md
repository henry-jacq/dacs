# DACS (Distributed Agent Control System)

A high-performance, OS-agnostic Distributed Agent Control System operating over persistent WebSocket connections. Built for remote management, offering robust bi-directional interactive shells and large-scale file streaming.

## 🚀 Key Features

- **Asynchronous WebSocket Architecture**: Lightweight, high-concurrency server handling numerous remote clients using a custom JSON-RPC style event loop.
- **Interactive Reverse TTY**: Full pseudo-terminal (PTY) allocation on POSIX systems with seamless Windows standard-pipe fallback. Creates native raw-mode shell environments complete with command auto-completion and stable process isolation.
- **Chunked File Transfer**: Built-in support for streaming massive files over WebSockets (`upload` and `download`) using background encoding to completely prevent memory explosion.
- **Dynamic Task Dispatch**: Send native dynamic tasks easily from the server to specific client agents asynchronously without blocking the central heartbeat multiplexer.

---

## 📂 Project Structure

```text
server/
  app/            # Async WebSocket Controller & Interactive Tactical Console
  config/         # Server Configuration

client/
  app/            # Reconnecting Agent, Executor, TTY, & Transfer components
  config/         # Client Configuration

deploy/apache/    # Apache Reverse-Proxy (WSS) reference
```

## ⚙️ Configuration

Configuration values are dynamically loaded overriding defaults in the following precise order:
1. `.env` files
2. JSON configuration files
3. Active system Environment Variables
4. Application defaults

## ⚡ Quick Start

### 1. Installation

```bash
cd /home/henry/dacs
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Environment

Copy the example configurations to bootstrap your setup:
```bash
cp server/config/.env.example server/config/.env
cp server/config/server.example.json server/config/server.json
cp client/config/.env.example client/config/.env
cp client/config/client.example.json client/config/client.json
```
**Important**: Ensure the `DACS_AGENT_TOKEN` is identical across both the server and initialized clients. Customize `DACS_CLIENT_ID` uniquely for each client prior to running.

### 3. Launching DACS

**Start the Server:**
```bash
python -m server.app.main
```

**Start the Client:**
```bash
python -m client.app.main
```

---

## 💻 Server Console Commands

The DACS Command Line Interface operates via a tactical console tracking connected clients and managing interactive task states.

### Core Workflow
- `help`: View all available commands
- `sessions` / `clients`: List currently connected active nodes
- `use <client_id>`: Enter an interactive session targeting a specific client
- `back`: Exit the active context session
- `clear` / `cls`: Clear the console output
- `quit`: Shutdown the server cleanly

### Session Commands (Requires `use <client_id>`)
- `tty`: Initiates a raw, bi-directional interactive OS shell. Press `Ctrl+X` to gracefully detach and background the shell process.
- `upload <local_path> <remote_path>`: Streams a file to the remote client. Safely auto-expands `~` and resolves paths using the native `transfers/` isolation directory logic.
- `download <remote_path> <local_path>`: Streams a file from the client. Leaving `<local_path>` blank automatically routes the file securely into the local server's `transfers/` folder.
- `run <action_name> [payload_json]`: Execute a designated action template interactively or via hardcoded JSON payload.

### Available Actions (`run`)
- `restart_agent`

### Task History
- `task <task_id>`: Query a task's full result details in JSON

---

## 🌐 Secure Deployment (WSS)

For deploying over an encrypted `wss://` protocol:
Use the provided [dacs.conf](deploy/apache/dacs.conf) to setup an Apache reverse-proxy routing your secure WebSocket layer proxying `/ws` into the private listening socket on `ws://127.0.0.1:8080/ws`.
