"""
tests/test_registry.py - Unit tests for the UnifiedToolRegistry.
"""
from __future__ import annotations

import asyncio
import pytest

from proxy.registry import UnifiedToolRegistry
from shared.models import MCPTool, TransportType


@pytest.mark.asyncio
async def test_register_and_lookup():
    registry = UnifiedToolRegistry()
    tool = MCPTool(name="ping", description="Test ping tool")
    await registry.register_tools([tool], "mcp1", TransportType.WEBSOCKET)

    entry = await registry.lookup("ping")
    assert entry is not None
    assert entry.downstream_id == "mcp1"
    assert entry.tool.name == "ping"


@pytest.mark.asyncio
async def test_lookup_missing_tool():
    registry = UnifiedToolRegistry()
    entry = await registry.lookup("nonexistent_tool")
    assert entry is None


@pytest.mark.asyncio
async def test_deregister_downstream():
    registry = UnifiedToolRegistry()
    tools = [
        MCPTool(name="search_docs"),
        MCPTool(name="summarize_text"),
    ]
    await registry.register_tools(tools, "mcp1", TransportType.WEBSOCKET)
    assert await registry.tool_count() == 2

    removed = await registry.deregister_downstream("mcp1")
    assert removed == 2
    assert await registry.tool_count() == 0


@pytest.mark.asyncio
async def test_multi_downstream_registry():
    registry = UnifiedToolRegistry()
    tools_mcp1 = [MCPTool(name="ping"), MCPTool(name="search_docs")]
    tools_thunai = [MCPTool(name="weather"), MCPTool(name="translate")]

    await registry.register_tools(tools_mcp1, "mcp1", TransportType.WEBSOCKET)
    await registry.register_tools(tools_thunai, "thunai", TransportType.SSE)

    assert await registry.tool_count() == 4

    entry = await registry.lookup("weather")
    assert entry is not None
    assert entry.downstream_id == "thunai"


@pytest.mark.asyncio
async def test_record_call_metrics():
    registry = UnifiedToolRegistry()
    tool = MCPTool(name="ping")
    await registry.register_tools([tool], "mcp1", TransportType.WEBSOCKET)

    await registry.record_call("ping", 50.0, error=False)
    await registry.record_call("ping", 30.0, error=True)

    entry = await registry.lookup("ping")
    assert entry.call_count == 2
    assert entry.error_count == 1
    assert entry.avg_latency_ms == 40.0


@pytest.mark.asyncio
async def test_all_tools_returns_list():
    registry = UnifiedToolRegistry()
    tools = [MCPTool(name=f"tool_{i}") for i in range(5)]
    await registry.register_tools(tools, "mcp1", TransportType.WEBSOCKET)

    all_tools = await registry.all_tools()
    assert len(all_tools) == 5
