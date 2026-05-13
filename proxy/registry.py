"""
proxy/registry.py - Unified in-memory tool registry with metrics tracking.

The registry is the single source of truth for:
  - Which tools exist across ALL downstream MCPs
  - Which downstream owns each tool
  - Per-tool call statistics
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

import structlog

from shared.models import MCPTool, ToolRegistry, ToolRegistryEntry, TransportType

log = structlog.get_logger(__name__)


class UnifiedToolRegistry:
    """
    Thread-safe, async-compatible unified tool registry.

    Wraps the Pydantic ToolRegistry with asyncio locking and
    provides a rich query API used by the router.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._registry = ToolRegistry()

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def register_tools(
        self,
        tools: List[MCPTool],
        downstream_id: str,
        transport: TransportType,
    ) -> int:
        """
        Register (or update) all tools from a downstream MCP server.
        Returns the number of tools registered.
        """
        async with self._lock:
            count = 0
            for tool in tools:
                self._registry.register(tool, downstream_id, transport)
                count += 1
                log.debug(
                    "tool_registered",
                    tool=tool.name,
                    downstream=downstream_id,
                )
            log.info(
                "tools_registered_batch",
                downstream=downstream_id,
                count=count,
                total=len(self._registry.entries),
            )
            return count

    async def deregister_downstream(self, downstream_id: str) -> int:
        """
        Remove all tools belonging to a downstream MCP.
        Called when a downstream disconnects.
        Returns number of tools removed.
        """
        async with self._lock:
            to_remove = [
                name
                for name, entry in self._registry.entries.items()
                if entry.downstream_id == downstream_id
            ]
            for name in to_remove:
                del self._registry.entries[name]

            if to_remove:
                log.info(
                    "tools_deregistered",
                    downstream=downstream_id,
                    removed=len(to_remove),
                    tools=to_remove,
                )
            return len(to_remove)

    async def record_call(
        self,
        tool_name: str,
        latency_ms: float,
        error: bool = False,
    ) -> None:
        async with self._lock:
            self._registry.record_call(tool_name, latency_ms, error)

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def lookup(self, tool_name: str) -> Optional[ToolRegistryEntry]:
        async with self._lock:
            return self._registry.lookup(tool_name)

    async def all_tools(self) -> List[MCPTool]:
        async with self._lock:
            return self._registry.all_tools()

    async def all_entries(self) -> Dict[str, ToolRegistryEntry]:
        async with self._lock:
            return dict(self._registry.entries)

    async def tool_count(self) -> int:
        async with self._lock:
            return len(self._registry.entries)

    async def downstream_tool_names(self, downstream_id: str) -> List[str]:
        async with self._lock:
            return [
                name
                for name, e in self._registry.entries.items()
                if e.downstream_id == downstream_id
            ]

    async def summary(self) -> Dict[str, Any]:
        """Return a human-readable summary of the registry."""
        async with self._lock:
            mapping: Dict[str, List[str]] = {}
            for name, entry in self._registry.entries.items():
                mapping.setdefault(entry.downstream_id, []).append(name)
            return {
                "total_tools": len(self._registry.entries),
                "last_updated": self._registry.last_updated,
                "by_downstream": mapping,
            }
