"""
proxy/session_manager.py - Manages persistent connections to downstream MCP servers.

Responsibilities:
  - Maintain one transport per downstream server
  - Handle MCP initialize handshake
  - Perform dynamic tool discovery (tools/list)
  - Reconnect with exponential back-off
  - Heartbeat monitoring
  - Circuit breaker per downstream
  - Expose send_tool_call() API to the router
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

import structlog

from proxy.registry import UnifiedToolRegistry
from proxy.transports.base import BaseTransport
from proxy.transports.sse_transport import SSETransport
from proxy.transports.websocket_transport import WebSocketTransport
from shared.jsonrpc import (
    MCPMethod,
    build_initialize_request,
    build_tools_list_request,
    build_tools_call_request,
    make_notification,
    encode,
)
from shared.models import (
    CircuitState,
    ConnectionState,
    DownstreamConfig,
    DownstreamHealth,
    MCPTool,
    TransportType,
)
from shared.utils import CircuitBreaker, async_timer

log = structlog.get_logger(__name__)


class DownstreamSession:
    """
    Manages a single downstream MCP server:
      transport → initialize → tools/list → steady-state operations
    """

    def __init__(
        self,
        config: DownstreamConfig,
        registry: UnifiedToolRegistry,
    ) -> None:
        self.config = config
        self.registry = registry
        self._transport: Optional[BaseTransport] = None
        self._connected = False
        self._initialized = False
        self._last_connected: Optional[float] = None
        self._last_error: Optional[str] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._cb = CircuitBreaker(
            failure_threshold=config.cb_failure_threshold,
            recovery_timeout_s=config.cb_recovery_timeout_s,
            name=config.id,
        )

    # ------------------------------------------------------------------
    # Transport factory
    # ------------------------------------------------------------------

    def _create_transport(self) -> BaseTransport:
        if self.config.transport == TransportType.WEBSOCKET:
            return WebSocketTransport(
                downstream_id=self.config.id,
                url=self.config.url,
                headers=self.config.headers,
            )
        elif self.config.transport == TransportType.SSE:
            return SSETransport(
                downstream_id=self.config.id,
                url=self.config.url,
                headers=self.config.headers,
                request_timeout_s=self.config.request_timeout_s,
            )
        else:
            raise ValueError(f"Unsupported transport: {self.config.transport}")

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start connection with automatic reconnect loop."""
        self._reconnect_task = asyncio.create_task(
            self._connect_with_retry(), name=f"reconnect-{self.config.id}"
        )
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(), name=f"heartbeat-{self.config.id}"
        )

    async def stop(self) -> None:
        """Gracefully shut down this downstream session."""
        log.info("downstream_stopping", downstream=self.config.id)
        self._connected = False
        self._initialized = False

        for task in (self._reconnect_task, self._heartbeat_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        if self._transport:
            await self._transport.disconnect()

        await self.registry.deregister_downstream(self.config.id)

    async def _connect_with_retry(self) -> None:
        """
        Attempt connection with exponential back-off up to max_reconnect_attempts.
        After a successful connection, loop forever reconnecting on disconnect.
        """
        attempt = 0
        delay = self.config.reconnect_interval_s

        while True:
            if not self.config.enabled:
                await asyncio.sleep(5)
                continue

            try:
                attempt += 1
                log.info(
                    "downstream_connect_attempt",
                    downstream=self.config.id,
                    attempt=attempt,
                )
                await self._do_connect()
                attempt = 0  # Reset on success
                delay = self.config.reconnect_interval_s
                # Wait here until the transport disconnects
                await self._wait_for_disconnect()

            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._last_error = str(exc)
                self._connected = False
                self._initialized = False
                log.error(
                    "downstream_connect_failed",
                    downstream=self.config.id,
                    attempt=attempt,
                    error=str(exc),
                    retry_in=delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60.0)

    async def _do_connect(self) -> None:
        """Connect, initialize, and discover tools."""
        self._transport = self._create_transport()
        await self._transport.connect()
        self._last_connected = time.time()
        self._connected = True
        log.info("downstream_transport_connected", downstream=self.config.id)

        await self._mcp_initialize()
        await self._discover_tools()
        self._initialized = True
        self._cb.record_success()
        log.info("downstream_ready", downstream=self.config.id)

    async def _wait_for_disconnect(self) -> None:
        """Block until the transport reports disconnection."""
        while self._transport and self._transport.is_connected:
            await asyncio.sleep(1.0)
        self._connected = False
        self._initialized = False
        log.warning("downstream_disconnected_detected", downstream=self.config.id)

    # ------------------------------------------------------------------
    # MCP protocol handshakes
    # ------------------------------------------------------------------

    async def _mcp_initialize(self) -> None:
        """Perform MCP initialize handshake."""
        req = build_initialize_request(client_name="mcp-proxy-gateway")
        log.debug("mcp_initialize_sending", downstream=self.config.id)
        resp = await self._transport.send_and_receive(  # type: ignore[union-attr]
            req.model_dump(),
            timeout_s=self.config.request_timeout_s,
        )

        if "error" in resp and resp["error"]:
            raise RuntimeError(
                f"MCP initialize failed for {self.config.id}: {resp['error']}"
            )

        result = resp.get("result", {})
        proto_ver = result.get("protocolVersion", "unknown")
        server_name = result.get("serverInfo", {}).get("name", "unknown")
        log.info(
            "mcp_initialized",
            downstream=self.config.id,
            protocolVersion=proto_ver,
            serverName=server_name,
        )

        # Send initialized notification (no id)
        notif = make_notification(MCPMethod.INITIALIZED)
        await self._transport.send(notif.model_dump(exclude_none=True))

    async def _discover_tools(self) -> None:
        """Call tools/list and register all discovered tools."""
        req = build_tools_list_request()
        log.debug("tools_list_sending", downstream=self.config.id)
        resp = await self._transport.send_and_receive(  # type: ignore[union-attr]
            req.model_dump(),
            timeout_s=self.config.request_timeout_s,
        )

        if "error" in resp and resp["error"]:
            raise RuntimeError(
                f"tools/list failed for {self.config.id}: {resp['error']}"
            )

        raw_tools: List[Dict[str, Any]] = resp.get("result", {}).get("tools", [])
        tools = [MCPTool(**t) for t in raw_tools]

        count = await self.registry.register_tools(
            tools, self.config.id, self.config.transport
        )
        log.info("tool_discovery_complete", downstream=self.config.id, tool_count=count)

    # ------------------------------------------------------------------
    # Heartbeat loop
    # ------------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        """Periodically check connection health."""
        while True:
            try:
                await asyncio.sleep(self.config.heartbeat_interval_s)
                if self._transport and self._transport.is_connected:
                    alive = await self._transport.heartbeat()
                    if not alive:
                        log.warning("heartbeat_failed", downstream=self.config.id)
                        self._cb.record_failure()
                    else:
                        log.debug("heartbeat_ok", downstream=self.config.id)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("heartbeat_error", downstream=self.config.id, error=str(exc))

    # ------------------------------------------------------------------
    # Tool call execution
    # ------------------------------------------------------------------

    async def call_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        timeout_s: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Execute a tools/call on this downstream and return the JSON-RPC response dict.
        Applies circuit breaker and timeout.
        """
        if self._cb.is_open:
            raise RuntimeError(
                f"[{self.config.id}] Circuit breaker OPEN – downstream unavailable"
            )
        if not self._initialized:
            raise RuntimeError(
                f"[{self.config.id}] Downstream not yet initialized"
            )

        timeout = timeout_s or self.config.request_timeout_s
        req = build_tools_call_request(tool_name, arguments)

        async def _do_call() -> Dict[str, Any]:
            return await self._transport.send_and_receive(  # type: ignore[union-attr]
                req.model_dump(),
                timeout_s=timeout,
            )

        try:
            result = await self._cb.call(_do_call)
            return result
        except Exception as exc:
            self._last_error = str(exc)
            raise

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def health(self) -> DownstreamHealth:
        state = (
            ConnectionState.CONNECTED if self._connected else ConnectionState.DISCONNECTED
        )
        cb_state_str = self._cb.state
        cb_state = CircuitState(cb_state_str) if cb_state_str in CircuitState.__members__.values() else CircuitState.CLOSED

        return DownstreamHealth(
            downstream_id=self.config.id,
            name=self.config.name,
            state=state,
            circuit_state=cb_state,
            tool_count=0,  # Filled in by SessionManager after registry lookup
            last_connected=self._last_connected,
            last_error=self._last_error,
        )


class SessionManager:
    """
    Manages all downstream MCP sessions.
    Provides the unified call interface used by the proxy router.
    """

    def __init__(self, registry: UnifiedToolRegistry) -> None:
        self.registry = registry
        self._sessions: Dict[str, DownstreamSession] = {}

    def add_downstream(self, config: DownstreamConfig) -> None:
        """Register a downstream configuration (call before start())."""
        session = DownstreamSession(config, self.registry)
        self._sessions[config.id] = session
        log.info("downstream_registered", downstream=config.id, transport=config.transport.value)

    async def start_all(self) -> None:
        """Start all downstream sessions concurrently."""
        await asyncio.gather(*[s.start() for s in self._sessions.values()])

    async def stop_all(self) -> None:
        """Stop all downstream sessions gracefully."""
        await asyncio.gather(*[s.stop() for s in self._sessions.values()])

    async def call_tool(
        self,
        downstream_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        timeout_s: Optional[float] = None,
    ) -> Dict[str, Any]:
        session = self._sessions.get(downstream_id)
        if session is None:
            raise KeyError(f"No session for downstream: {downstream_id}")
        return await session.call_tool(tool_name, arguments, timeout_s=timeout_s)

    async def health(self) -> List[DownstreamHealth]:
        reports = []
        for sid, session in self._sessions.items():
            h = session.health()
            h.tool_count = len(await self.registry.downstream_tool_names(sid))
            reports.append(h)
        return reports

    def get_session(self, downstream_id: str) -> Optional[DownstreamSession]:
        return self._sessions.get(downstream_id)
