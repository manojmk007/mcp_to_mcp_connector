"""
clients/thunai_client.py - Thunai MCP SSE client.

Connects to: https://api.thunai.ai/mcp-service/thunai/service/mcp-sse/mcp
Transport: SSE (Server-Sent Events)

This module is a THIN WRAPPER over SSETransport that adds Thunai-specific:
  - Session initialization
  - Tool discovery
  - Tool invocation
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional

import structlog

from proxy.transports.sse_transport import SSETransport
from shared.jsonrpc import (
    build_initialize_request,
    build_tools_call_request,
    build_tools_list_request,
    make_notification,
    MCPMethod,
)
from shared.models import MCPTool
from shared.utils import retry_async

log = structlog.get_logger(__name__)

THUNAI_MCP_URL = os.getenv(
    "THUNAI_MCP_URL",
    "https://api.thunai.ai/mcp-service/thunai/service/mcp-sse/mcp",
)


class ThunaiMCPClient:
    """
    Standalone Thunai MCP client (useful for testing outside the proxy).
    For production use, the proxy uses SSETransport directly via SessionManager.
    """

    def __init__(
        self,
        url: str = THUNAI_MCP_URL,
        headers: Optional[Dict[str, str]] = None,
        request_timeout_s: float = 30.0,
    ) -> None:
        self._url = url
        self._headers = headers or {}
        self._timeout = request_timeout_s
        self._transport: Optional[SSETransport] = None
        self._initialized = False
        self._tools: List[MCPTool] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Connect to Thunai MCP and perform initialize + tool discovery."""
        log.info("thunai_connecting", url=self._url)
        self._transport = SSETransport(
            downstream_id="thunai",
            url=self._url,
            headers=self._headers,
            request_timeout_s=self._timeout,
        )

        await retry_async(
            self._transport.connect,
            max_attempts=5,
            base_delay_s=2.0,
            exceptions=(ConnectionError, asyncio.TimeoutError),
        )

        await self._initialize()
        await self._discover_tools()
        log.info("thunai_ready", tool_count=len(self._tools))

    async def disconnect(self) -> None:
        if self._transport:
            await self._transport.disconnect()
        self._initialized = False
        self._tools.clear()

    async def _initialize(self) -> None:
        req = build_initialize_request(client_name="thunai-direct-client")
        resp = await self._transport.send_and_receive(  # type: ignore[union-attr]
            req.model_dump(), timeout_s=self._timeout
        )
        if resp.get("error"):
            raise RuntimeError(f"Thunai initialize failed: {resp['error']}")

        result = resp.get("result", {})
        log.info(
            "thunai_initialized",
            protocolVersion=result.get("protocolVersion"),
            serverName=result.get("serverInfo", {}).get("name"),
        )

        # Send initialized notification
        notif = make_notification(MCPMethod.INITIALIZED)
        await self._transport.send(notif.model_dump(exclude_none=True))
        self._initialized = True

    async def _discover_tools(self) -> None:
        req = build_tools_list_request()
        resp = await self._transport.send_and_receive(  # type: ignore[union-attr]
            req.model_dump(), timeout_s=self._timeout
        )
        if resp.get("error"):
            raise RuntimeError(f"Thunai tools/list failed: {resp['error']}")

        raw_tools = resp.get("result", {}).get("tools", [])
        self._tools = [MCPTool(**t) for t in raw_tools]
        log.info("thunai_tools_discovered", tools=[t.name for t in self._tools])

    # ------------------------------------------------------------------
    # Tool operations
    # ------------------------------------------------------------------

    @property
    def tools(self) -> List[MCPTool]:
        return list(self._tools)

    async def call_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        timeout_s: Optional[float] = None,
    ) -> Dict[str, Any]:
        if not self._initialized:
            raise RuntimeError("ThunaiMCPClient not initialized – call connect() first")

        req = build_tools_call_request(tool_name, arguments)
        timeout = timeout_s or self._timeout

        log.info("thunai_tool_call", tool=tool_name, arguments=str(arguments)[:200])
        resp = await self._transport.send_and_receive(  # type: ignore[union-attr]
            req.model_dump(), timeout_s=timeout
        )

        if resp.get("error"):
            log.error("thunai_tool_error", tool=tool_name, error=resp["error"])

        return resp

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "ThunaiMCPClient":
        await self.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.disconnect()


# ---------------------------------------------------------------------------
# Demo script
# ---------------------------------------------------------------------------

async def _demo() -> None:
    """Quick connectivity test – run with: python -m clients.thunai_client"""
    import structlog
    from shared.logging_config import configure_logging
    configure_logging(level="DEBUG")

    async with ThunaiMCPClient() as client:
        print(f"\n✅ Connected to Thunai MCP")
        print(f"📦 Discovered {len(client.tools)} tools:")
        for tool in client.tools:
            print(f"   • {tool.name}: {tool.description or '(no description)'}")


if __name__ == "__main__":
    asyncio.run(_demo())
