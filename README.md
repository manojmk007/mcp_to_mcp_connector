# MCP Proxy Gateway

> **Pattern 2 — Thin Proxy / Bridge Architecture**
> A production-grade MCP Gateway that federates multiple MCP servers behind one unified MCP interface.

---

## Architecture

```
Client / Host  (any MCP client: Claude Desktop, LangChain, custom)
       │
       │   WebSocket ws://proxy:8765/ws
       │   SSE        http://proxy:8088/sse + POST /messages  
       │   HTTP       POST http://proxy:8088/mcp
       ▼
┌─────────────────────────────────────────────────────────────┐
│                  MCP Proxy Gateway                          │
│                                                             │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │  WS Server  │  │  HTTP Server │  │  SSE Server      │  │
│  └──────┬──────┘  └──────┬───────┘  └────────┬─────────┘  │
│         └────────────────┼──────────────────┘             │
│                          ▼                                  │
│              ┌───────────────────────┐                     │
│              │   Middleware Pipeline │                     │
│              │  (trace, log, metrics)│                     │
│              └───────────┬───────────┘                     │
│                          ▼                                  │
│              ┌───────────────────────┐                     │
│              │     Tool Router       │ ◄── registry lookup │
│              └───────────┬───────────┘                     │
│                          ▼                                  │
│              ┌───────────────────────┐                     │
│              │   Session Manager     │                     │
│              │  (one session/ds MCP) │                     │
│              └─────────┬─────────────┘                     │
│                        │                                   │
│           ┌────────────┴───────────────┐                   │
│           ▼                            ▼                   │
│  ┌─────────────────┐        ┌──────────────────────┐       │
│  │  WS Transport   │        │   SSE Transport       │      │
│  └────────┬────────┘        └──────────┬────────────┘      │
└───────────┼─────────────────────────────┼───────────────────┘
            │                             │
            ▼                             ▼
    ┌───────────────┐           ┌──────────────────────┐
    │  MCP1 Local   │           │  Thunai MCP (SSE)    │
    │  ws://9001    │           │  api.thunai.ai/...   │
    │               │           │                      │
    │  • search_docs│           │  • (dynamically      │
    │  • summarize  │           │     discovered)      │
    │  • ping       │           │                      │
    └───────────────┘           └──────────────────────┘
```

---

## Project Structure

```
mcp_to_mcp/
│
├── main.py                    # Main entrypoint
├── config.py                  # Pydantic-settings config loader
├── requirements.txt           # Python dependencies
├── .env.example               # Environment template
├── pytest.ini                 # Test configuration
├── Dockerfile                 # Multi-stage Docker build
├── docker-compose.yml         # Full-stack Docker Compose
├── start.bat                  # Windows startup script
├── start.sh                   # Linux/Mac startup script
│
├── proxy/                     # Core proxy implementation
│   ├── server.py              # Unified MCP proxy server (WS + SSE + HTTP)
│   ├── router.py              # Tool routing engine
│   ├── registry.py            # Unified in-memory tool registry
│   ├── session_manager.py     # Downstream session lifecycle
│   ├── transports/
│   │   ├── base.py            # Abstract transport interface
│   │   ├── websocket_transport.py
│   │   └── sse_transport.py
│   └── middleware/
│       └── __init__.py        # Logging, tracing, metrics middleware
│
├── mcp1/                      # Custom local MCP server
│   ├── server.py              # WebSocket MCP server
│   ├── tools/
│   │   ├── search_docs.py     # Document search tool
│   │   └── summarize_text.py  # Text summarization tool
│   └── handlers/
│       └── tool_handler.py    # JSON-RPC tool dispatcher
│
├── clients/
│   └── thunai_client.py       # Thunai SSE MCP client (standalone)
│
├── shared/
│   ├── models.py              # Pydantic data models
│   ├── jsonrpc.py             # JSON-RPC 2.0 helpers
│   ├── utils.py               # Circuit breaker, retry, timer
│   └── logging_config.py      # Structured logging setup
│
├── examples/
│   └── example_client.py      # Demo client (WS, HTTP, health check)
│
└── tests/
    ├── test_registry.py
    ├── test_jsonrpc.py
    ├── test_mcp1_tools.py
    └── test_router.py
```

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Start MCP1 server (in one terminal)

```bash
python -m mcp1.server --port 9001
```

### 3. Start the proxy (in another terminal)

```bash
python main.py --external-mcp1
```

Or use the convenience scripts:

```bash
# Windows
start.bat

# Linux / Mac
chmod +x start.sh && ./start.sh
```

### 4. Run the demo client

```bash
# WebSocket demo
python examples/example_client.py

# HTTP demo
python examples/example_client.py --transport http

# Health check
python examples/example_client.py --transport health
```

---

## Endpoints

| Endpoint | Transport | Description |
|---|---|---|
| `ws://localhost:8765/ws` | WebSocket | Primary MCP endpoint (full-duplex) |
| `GET http://localhost:8088/sse` | SSE | Server-sent events stream |
| `POST http://localhost:8088/messages` | HTTP | Client → server for SSE sessions |
| `POST http://localhost:8088/mcp` | HTTP | Simple JSON-RPC request/response |
| `GET http://localhost:8088/health` | HTTP | Health report |
| `GET http://localhost:8088/metrics` | HTTP | Proxy metrics |
| `GET http://localhost:8088/registry` | HTTP | Tool registry snapshot |
| `GET http://localhost:8088/traces` | HTTP | Recent tool call traces |

---

## MCP Protocol Flow

### Startup
```
proxy.start()
  → session_manager.start_all()
    → [mcp1] WebSocketTransport.connect()
    → [mcp1] MCP initialize handshake
    → [mcp1] tools/list → register 3 tools
    → [thunai] SSETransport.connect()
    → [thunai] SSE endpoint discovery
    → [thunai] MCP initialize handshake
    → [thunai] tools/list → register N tools
  → registry built: {"search_docs": "mcp1", "ping": "mcp1", ...}
  → WebSocket server started on :8765
  → HTTP server started on :8088
```

### Tool Call
```
client → proxy: {"method": "tools/call", "params": {"name": "search_docs", "arguments": {...}}}
  → middleware pipeline (trace, log, metrics)
  → router.handle_tools_call("search_docs", {...})
    → registry.lookup("search_docs") → entry{downstream_id="mcp1"}
    → session_manager.call_tool("mcp1", "search_docs", {...})
      → circuit_breaker.call(...)
        → WebSocketTransport.send_and_receive(...)
          → mcp1 server executes search_docs()
          → returns result
    → record metrics
    → return result
proxy → client: {"jsonrpc": "2.0", "result": {...}, "id": "..."}
```

---

## Production Features

| Feature | Implementation |
|---|---|
| **Async** | `asyncio` throughout, no blocking calls |
| **Transport abstraction** | `BaseTransport` → `WebSocketTransport` / `SSETransport` |
| **Dynamic tool discovery** | `tools/list` on every connection, in-memory registry |
| **Circuit breaker** | Per-downstream, configurable thresholds |
| **Auto-reconnect** | Exponential back-off, runs forever |
| **Heartbeat** | Per-downstream ping loop |
| **Structured logging** | `structlog`, JSON or colored console |
| **Request tracing** | `trace_id` bound to every request context |
| **Metrics** | Per-method counts, latency, error rate |
| **Health API** | `/health` endpoint with downstream status |
| **Middleware** | Pluggable pipeline (log → trace → metrics) |
| **Graceful shutdown** | SIGINT/SIGTERM → stop all sessions |
| **Docker** | Multi-stage Dockerfile + Compose |
| **Tests** | pytest-asyncio unit tests |

---

## Docker Deployment

```bash
# Build and start all services
docker-compose up --build

# Proxy-only (if MCP1 is external)
docker-compose up proxy

# View logs
docker-compose logs -f proxy

# Health check
curl http://localhost:8088/health | python -m json.tool
```

---

## Configuration

All settings can be overridden via environment variables or `.env` file:

```env
PROXY_HOST=0.0.0.0
PROXY_WS_PORT=8765
PROXY_HTTP_PORT=8088
LOG_LEVEL=INFO
JSON_LOGS=false
MCP1_HOST=127.0.0.1
MCP1_PORT=9001
THUNAI_MCP_URL=https://api.thunai.ai/mcp-service/thunai/service/mcp-sse/mcp
CB_FAILURE_THRESHOLD=5
CB_RECOVERY_TIMEOUT_S=30.0
REQUEST_TIMEOUT_S=30.0
```

---

## Running Tests

```bash
pytest tests/ -v
```

---

## Future Roadmap

- [ ] OAuth 2.0 / Bearer token auth per downstream
- [ ] Redis-backed distributed tool registry
- [ ] Multi-user sessions with RBAC
- [ ] OpenTelemetry tracing integration
- [ ] Prometheus `/metrics` endpoint
- [ ] LangSmith tracing hook
- [ ] Streaming LLM response passthrough
- [ ] Kubernetes Helm chart
- [ ] gRPC transport adapter
- [ ] Hot-reload of downstream configurations
