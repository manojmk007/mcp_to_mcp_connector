"""
proxy/server.py - Unified MCP Proxy Server.

Exposes THREE transport endpoints for upstream clients:
  1. WebSocket  ws://host:port/ws        – full-duplex MCP
  2. SSE        GET  http://host:port/sse  – event stream
               POST http://host:port/messages – client → server
  3. HTTP JSON  POST http://host:port/mcp  – simple request/response

Admin endpoints:
  GET /health     – health report
  GET /metrics    – proxy metrics
  GET /registry   – tool registry snapshot
  GET /traces     – recent tool call traces
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional

import structlog
import websockets
from aiohttp import web

from proxy.middleware import (
    MetricsMiddleware,
    MiddlewarePipeline,
    RequestLoggingMiddleware,
    TracingMiddleware,
)
from proxy.registry import UnifiedToolRegistry
from proxy.router import ToolRouter
from proxy.session_manager import SessionManager
from shared.jsonrpc import (
    ErrorCode,
    MCPMethod,
    make_error,
    make_success,
    validate_jsonrpc,
)
from shared.models import (
    ClientSession,
    DownstreamConfig,
    HealthReport,
    MCPCapabilities,
    MCPServerInfo,
    ProxyMetrics,
    TransportType,
)
from shared.logging_config import configure_logging

log = structlog.get_logger(__name__)

_PROXY_NAME = "MCP-Proxy-Gateway"
_PROXY_VERSION = "1.0.0"
_PROTOCOL_VERSION = "2024-11-05"


class MCPProxyServer:
    """
    The unified MCP Proxy Gateway.

    Usage:
        server = MCPProxyServer(config)
        await server.start()
        ...
        await server.stop()
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        ws_port: int = 8765,
        http_port: int = 8088,
        log_level: str = "INFO",
        json_logs: bool = False,
    ) -> None:
        self.host = host
        self.ws_port = ws_port
        self.http_port = http_port
        self._started_at = time.time()

        # Core components
        self.registry = UnifiedToolRegistry()
        self.session_manager = SessionManager(self.registry)
        self.router = ToolRouter(self.registry, self.session_manager)

        # Middleware pipeline
        self._metrics_mw = MetricsMiddleware()
        self._pipeline = MiddlewarePipeline([
            TracingMiddleware(),
            RequestLoggingMiddleware(),
            self._metrics_mw,
        ])

        # Client sessions (upstream)
        self._client_sessions: Dict[str, ClientSession] = {}

        # Server handles (for graceful shutdown)
        self._ws_server: Optional[Any] = None
        self._http_runner: Optional[web.AppRunner] = None

        configure_logging(level=log_level, json_output=json_logs)

    # ------------------------------------------------------------------
    # Configuration API
    # ------------------------------------------------------------------

    def add_downstream(self, config: DownstreamConfig) -> None:
        """Register a downstream MCP server before starting."""
        self.session_manager.add_downstream(config)

    # ------------------------------------------------------------------
    # Startup / Shutdown
    # ------------------------------------------------------------------

    async def start(self) -> None:
        log.info("proxy_starting", name=_PROXY_NAME, version=_PROXY_VERSION)

        # 1. Start downstream sessions (connect + tool discovery)
        await self.session_manager.start_all()

        # 2. Brief wait to allow sessions to initialise
        await asyncio.sleep(2)

        tool_count = await self.registry.tool_count()
        log.info("registry_ready", tool_count=tool_count)

        # 3. Start upstream servers
        await self._start_websocket_server()
        await self._start_http_server()

        log.info(
            "proxy_ready",
            ws=f"ws://{self.host}:{self.ws_port}/ws",
            http=f"http://{self.host}:{self.http_port}",
        )

    async def stop(self) -> None:
        log.info("proxy_stopping")

        if self._ws_server:
            self._ws_server.close()
            await self._ws_server.wait_closed()

        if self._http_runner:
            await self._http_runner.cleanup()

        await self.session_manager.stop_all()
        log.info("proxy_stopped")

    # ------------------------------------------------------------------
    # WebSocket server
    # ------------------------------------------------------------------

    async def _start_websocket_server(self) -> None:
        self._ws_server = await websockets.serve(
            self._ws_handler,
            self.host,
            self.ws_port,
            ping_interval=20,
            ping_timeout=10,
            max_size=10 * 1024 * 1024,
        )
        log.info("ws_server_started", host=self.host, port=self.ws_port)

    async def _ws_handler(self, websocket: Any, path: str = "/ws") -> None:
        """Handle a single WebSocket client connection."""
        remote = websocket.remote_address
        session = ClientSession(remote_addr=str(remote))
        self._client_sessions[session.session_id] = session

        log.info("ws_client_connected", session_id=session.session_id, remote=str(remote))

        try:
            async for raw in websocket:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError as exc:
                    err = make_error(ErrorCode.PARSE_ERROR, f"JSON parse error: {exc}")
                    await websocket.send(err.model_dump_json())
                    continue

                session.touch()
                session.request_count += 1

                response = await self._pipeline.execute(
                    data,
                    lambda req: self._dispatch(req, session),
                )
                await websocket.send(json.dumps(response))

        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as exc:
            log.exception("ws_client_error", session_id=session.session_id, error=str(exc))
        finally:
            self._client_sessions.pop(session.session_id, None)
            log.info("ws_client_disconnected", session_id=session.session_id)

    # ------------------------------------------------------------------
    # HTTP / SSE server (aiohttp)
    # ------------------------------------------------------------------

    async def _start_http_server(self) -> None:
        app = web.Application()
        app.router.add_post("/mcp", self._http_mcp_handler)
        app.router.add_get("/sse", self._sse_handler)
        app.router.add_post("/messages", self._sse_post_handler)
        app.router.add_get("/health", self._health_handler)
        app.router.add_get("/metrics", self._metrics_handler)
        app.router.add_get("/registry", self._registry_handler)
        app.router.add_get("/traces", self._traces_handler)

        self._http_runner = web.AppRunner(app)
        await self._http_runner.setup()
        site = web.TCPSite(self._http_runner, self.host, self.http_port)
        await site.start()
        log.info("http_server_started", host=self.host, port=self.http_port)

    async def _http_mcp_handler(self, request: web.Request) -> web.Response:
        """Simple HTTP POST JSON-RPC endpoint."""
        try:
            data = await request.json()
        except Exception as exc:
            err = make_error(ErrorCode.PARSE_ERROR, f"JSON parse error: {exc}")
            return web.Response(
                text=err.model_dump_json(),
                content_type="application/json",
                status=400,
            )

        session = ClientSession(remote_addr=str(request.remote))
        response = await self._pipeline.execute(
            data,
            lambda req: self._dispatch(req, session),
        )
        return web.Response(
            text=json.dumps(response),
            content_type="application/json",
        )

    # SSE-specific: we store active SSE connections and their send queues
    _sse_connections: Dict[str, asyncio.Queue] = {}

    async def _sse_handler(self, request: web.Request) -> web.StreamResponse:
        """SSE GET endpoint – stream server events to the client."""
        session = ClientSession(remote_addr=str(request.remote))
        queue: asyncio.Queue = asyncio.Queue()
        self._sse_connections[session.session_id] = queue

        response = web.StreamResponse(headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        })
        await response.prepare(request)

        # Send the post endpoint URL as the first event
        post_url = f"http://{self.host}:{self.http_port}/messages?session={session.session_id}"
        await response.write(f"event: endpoint\ndata: {post_url}\n\n".encode())

        log.info("sse_client_connected", session_id=session.session_id)

        try:
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                    await response.write(f"data: {json.dumps(msg)}\n\n".encode())
                except asyncio.TimeoutError:
                    # Send a keepalive comment
                    await response.write(b": keepalive\n\n")
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        finally:
            self._sse_connections.pop(session.session_id, None)
            log.info("sse_client_disconnected", session_id=session.session_id)

        return response

    async def _sse_post_handler(self, request: web.Request) -> web.Response:
        """SSE POST endpoint – receive client → server messages."""
        session_id = request.rel_url.query.get("session")
        queue = self._sse_connections.get(session_id) if session_id else None

        try:
            data = await request.json()
        except Exception as exc:
            return web.Response(status=400, text=str(exc))

        fake_session = ClientSession(session_id=session_id or "http", remote_addr=str(request.remote))
        response = await self._pipeline.execute(
            data,
            lambda req: self._dispatch(req, fake_session),
        )

        # If an SSE connection exists for this session, push the response there
        if queue:
            await queue.put(response)
            return web.Response(status=202)
        else:
            return web.Response(
                text=json.dumps(response),
                content_type="application/json",
            )

    # ------------------------------------------------------------------
    # Admin endpoints
    # ------------------------------------------------------------------

    async def _health_handler(self, request: web.Request) -> web.Response:
        uptime_s = time.time() - self._started_at
        downstream_health = await self.session_manager.health()
        tool_count = await self.registry.tool_count()

        any_healthy = any(
            h.state.value == "connected" for h in downstream_health
        )
        all_healthy = all(
            h.state.value == "connected" for h in downstream_health
        )
        status = "healthy" if all_healthy else ("degraded" if any_healthy else "unhealthy")

        report = HealthReport(
            status=status,
            uptime_s=uptime_s,
            tool_count=tool_count,
            downstream_health=downstream_health,
            metrics=self.router.get_metrics_snapshot(),
        )
        return web.Response(
            text=report.model_dump_json(indent=2),
            content_type="application/json",
        )

    async def _metrics_handler(self, request: web.Request) -> web.Response:
        data = {
            "proxy_metrics": self.router.get_metrics_snapshot().model_dump(),
            "middleware_metrics": self._metrics_mw.snapshot(),
        }
        return web.Response(text=json.dumps(data, indent=2), content_type="application/json")

    async def _registry_handler(self, request: web.Request) -> web.Response:
        summary = await self.registry.summary()
        return web.Response(text=json.dumps(summary, indent=2), content_type="application/json")

    async def _traces_handler(self, request: web.Request) -> web.Response:
        limit = int(request.rel_url.query.get("limit", "50"))
        traces = self.router.get_recent_traces(limit=limit)
        data = [t.model_dump() for t in traces]
        return web.Response(text=json.dumps(data, indent=2), content_type="application/json")

    # ------------------------------------------------------------------
    # Core JSON-RPC dispatcher
    # ------------------------------------------------------------------

    async def _dispatch(
        self, request: Dict[str, Any], session: ClientSession
    ) -> Dict[str, Any]:
        """
        Central dispatcher: routes incoming JSON-RPC calls to the correct handler.
        """
        err = validate_jsonrpc(request)
        if err:
            return make_error(ErrorCode.INVALID_REQUEST, err, req_id=request.get("id")).model_dump()

        method = request["method"]
        params = request.get("params") or {}
        req_id = request.get("id")

        # ------ MCP lifecycle ------
        if method == MCPMethod.INITIALIZE:
            return self._handle_initialize(params, req_id)

        if method == MCPMethod.PING:
            return make_success({"pong": True}, req_id).model_dump()

        # ------ Tools ------
        if method == MCPMethod.TOOLS_LIST:
            result = await self.router.handle_tools_list()
            return make_success(result, req_id).model_dump()

        if method == MCPMethod.TOOLS_CALL:
            tool_name = params.get("name")
            arguments = params.get("arguments", {})
            if not tool_name:
                return make_error(
                    ErrorCode.INVALID_PARAMS, "Missing 'name' in params", req_id=req_id
                ).model_dump()

            result = await self.router.handle_tools_call(
                tool_name=tool_name,
                arguments=arguments,
                session_id=session.session_id,
            )
            # result may already be a full JSON-RPC response dict (on error)
            if "jsonrpc" in result:
                result["id"] = req_id
                return result
            return make_success(result, req_id).model_dump()

        # ------ Unknown method ------
        return make_error(
            ErrorCode.METHOD_NOT_FOUND,
            f"Method not found: {method}",
            req_id=req_id,
        ).model_dump()

    def _handle_initialize(
        self, params: Dict[str, Any], req_id: Any
    ) -> Dict[str, Any]:
        """Handle MCP initialize handshake from an upstream client."""
        client_info = params.get("clientInfo", {})
        log.info(
            "client_initialized",
            client_name=client_info.get("name"),
            client_version=client_info.get("version"),
            protocol_version=params.get("protocolVersion"),
        )
        result = {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {
                "tools": {"listChanged": True},
            },
            "serverInfo": {
                "name": _PROXY_NAME,
                "version": _PROXY_VERSION,
            },
        }
        return make_success(result, req_id).model_dump()
