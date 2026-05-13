"""
tests/test_router.py - Unit tests for ToolRouter using mocked registry and session manager.
"""
from __future__ import annotations

import asyncio
import unittest.mock as mock

import pytest

from proxy.registry import UnifiedToolRegistry
from proxy.router import ToolRouter
from shared.models import MCPTool, ToolRegistryEntry, TransportType


def _make_registry_with_tool(tool_name: str, downstream_id: str) -> UnifiedToolRegistry:
    registry = UnifiedToolRegistry()
    asyncio.get_event_loop().run_until_complete(
        registry.register_tools(
            [MCPTool(name=tool_name, description="Test tool")],
            downstream_id,
            TransportType.WEBSOCKET,
        )
    )
    return registry


@pytest.mark.asyncio
async def test_tools_list_returns_all_tools():
    registry = UnifiedToolRegistry()
    tools = [MCPTool(name="ping"), MCPTool(name="search_docs")]
    await registry.register_tools(tools, "mcp1", TransportType.WEBSOCKET)

    session_mgr = mock.AsyncMock()
    router = ToolRouter(registry, session_mgr)

    result = await router.handle_tools_list()
    assert "tools" in result
    assert len(result["tools"]) == 2


@pytest.mark.asyncio
async def test_tools_call_routes_to_correct_downstream():
    registry = UnifiedToolRegistry()
    await registry.register_tools(
        [MCPTool(name="ping")], "mcp1", TransportType.WEBSOCKET
    )

    session_mgr = mock.AsyncMock()
    session_mgr.call_tool.return_value = {
        "result": {"content": [{"type": "text", "text": "pong"}], "isError": False}
    }

    router = ToolRouter(registry, session_mgr)
    result = await router.handle_tools_call("ping", {})

    session_mgr.call_tool.assert_called_once_with(
        downstream_id="mcp1",
        tool_name="ping",
        arguments={},
    )


@pytest.mark.asyncio
async def test_tools_call_tool_not_found():
    registry = UnifiedToolRegistry()
    session_mgr = mock.AsyncMock()
    router = ToolRouter(registry, session_mgr)

    result = await router.handle_tools_call("nonexistent", {})
    assert "error" in result
    assert result["error"]["code"] == -32000  # TOOL_NOT_FOUND


@pytest.mark.asyncio
async def test_metrics_incremented_on_success():
    registry = UnifiedToolRegistry()
    await registry.register_tools(
        [MCPTool(name="ping")], "mcp1", TransportType.WEBSOCKET
    )

    session_mgr = mock.AsyncMock()
    session_mgr.call_tool.return_value = {
        "result": {"content": [], "isError": False}
    }

    router = ToolRouter(registry, session_mgr)
    await router.handle_tools_call("ping", {})

    metrics = router.get_metrics_snapshot()
    assert metrics.total_requests == 1
    assert metrics.successful_requests == 1
    assert metrics.failed_requests == 0


@pytest.mark.asyncio
async def test_metrics_incremented_on_failure():
    registry = UnifiedToolRegistry()
    await registry.register_tools(
        [MCPTool(name="ping")], "mcp1", TransportType.WEBSOCKET
    )

    session_mgr = mock.AsyncMock()
    session_mgr.call_tool.side_effect = RuntimeError("Circuit open")

    router = ToolRouter(registry, session_mgr)
    result = await router.handle_tools_call("ping", {})

    assert "error" in result
    metrics = router.get_metrics_snapshot()
    assert metrics.failed_requests == 1
