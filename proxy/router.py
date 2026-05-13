"""
proxy/router.py - Tool routing engine.

Responsibilities:
  - Accept tool call requests from upstream clients
  - Resolve which downstream owns the tool via registry lookup
  - Forward the request to the session manager
  - Track latency, errors, and emit observability events
  - Apply middleware (logging, tracing, metrics)
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

import structlog

from proxy.registry import UnifiedToolRegistry
from proxy.session_manager import SessionManager
from shared.jsonrpc import ErrorCode, make_error, make_success
from shared.models import MCPTool, ProxyMetrics, ToolCallMetric
from shared.utils import async_timer, new_trace_id

log = structlog.get_logger(__name__)


class ToolRouter:
    """
    Routes tool calls to the correct downstream MCP session.
    All public methods return JSON-RPC response dicts.
    """

    def __init__(
        self,
        registry: UnifiedToolRegistry,
        session_manager: SessionManager,
    ) -> None:
        self.registry = registry
        self.session_manager = session_manager
        self.metrics = ProxyMetrics()
        self._recent_calls: List[ToolCallMetric] = []  # In-memory trace log (last N)
        self._max_traces = 1000

    # ------------------------------------------------------------------
    # Main entry points (called by the proxy server handlers)
    # ------------------------------------------------------------------

    async def handle_tools_list(self) -> Dict[str, Any]:
        """Handle a tools/list request – return all registered tools."""
        tools: List[MCPTool] = await self.registry.all_tools()
        tool_dicts = [t.model_dump(exclude_none=True) for t in tools]
        log.info("tools_list_served", count=len(tool_dicts))
        return {"tools": tool_dicts}

    async def handle_tools_call(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        session_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Route a tools/call to the correct downstream.
        Returns the JSON-RPC result dict (not the full response).
        Raises appropriate JSON-RPC errors on failure.
        """
        trace_id = trace_id or new_trace_id()
        metric = ToolCallMetric(
            trace_id=trace_id,
            session_id=session_id,
            tool_name=tool_name,
            downstream_id="unknown",
        )

        structlog.contextvars.bind_contextvars(trace_id=trace_id)
        log.info("tool_call_received", tool=tool_name, arguments=str(arguments)[:200])

        # 1. Look up the tool in the registry
        entry = await self.registry.lookup(tool_name)
        if entry is None:
            log.warning("tool_not_found", tool=tool_name)
            self.metrics.total_requests += 1
            self.metrics.failed_requests += 1
            return make_error(
                ErrorCode.TOOL_NOT_FOUND,
                f"Tool '{tool_name}' not found in registry",
            ).model_dump()

        metric.downstream_id = entry.downstream_id

        # 2. Forward to the session manager
        try:
            async with async_timer() as timer:
                raw_response = await self.session_manager.call_tool(
                    downstream_id=entry.downstream_id,
                    tool_name=tool_name,
                    arguments=arguments,
                )

            latency_ms = timer.elapsed_ms

            # 3. Record metrics
            metric.finish(success=True)
            await self.registry.record_call(tool_name, latency_ms, error=False)
            self._update_metrics(latency_ms, success=True)

            log.info(
                "tool_call_success",
                tool=tool_name,
                downstream=entry.downstream_id,
                latency_ms=round(latency_ms, 2),
            )
            self._store_trace(metric)

            # 4. Extract result payload from raw JSON-RPC response
            if "error" in raw_response and raw_response["error"]:
                err = raw_response["error"]
                return make_error(
                    err.get("code", ErrorCode.UPSTREAM_ERROR),
                    err.get("message", "Upstream error"),
                    data=err.get("data"),
                ).model_dump()

            return raw_response.get("result", {})

        except RuntimeError as exc:
            # Circuit breaker open or session not ready
            metric.finish(success=False, error=str(exc))
            self._store_trace(metric)
            await self.registry.record_call(tool_name, 0, error=True)
            self._update_metrics(0, success=False)
            log.error("tool_call_runtime_error", tool=tool_name, error=str(exc))
            return make_error(
                ErrorCode.DOWNSTREAM_UNAVAILABLE,
                str(exc),
            ).model_dump()

        except asyncio.TimeoutError:
            metric.finish(success=False, error="timeout")
            self._store_trace(metric)
            await self.registry.record_call(tool_name, 0, error=True)
            self._update_metrics(0, success=False)
            log.error("tool_call_timeout", tool=tool_name)
            return make_error(ErrorCode.TIMEOUT, f"Tool call timed out: {tool_name}").model_dump()

        except Exception as exc:
            metric.finish(success=False, error=str(exc))
            self._store_trace(metric)
            await self.registry.record_call(tool_name, 0, error=True)
            self._update_metrics(0, success=False)
            log.exception("tool_call_unexpected_error", tool=tool_name, error=str(exc))
            return make_error(
                ErrorCode.INTERNAL_ERROR,
                f"Internal error routing tool '{tool_name}': {exc}",
            ).model_dump()

        finally:
            structlog.contextvars.unbind_contextvars("trace_id")

    # ------------------------------------------------------------------
    # Metrics helpers
    # ------------------------------------------------------------------

    def _update_metrics(self, latency_ms: float, success: bool) -> None:
        self.metrics.total_requests += 1
        self.metrics.total_latency_ms += latency_ms
        if success:
            self.metrics.successful_requests += 1
        else:
            self.metrics.failed_requests += 1

    def _store_trace(self, metric: ToolCallMetric) -> None:
        self._recent_calls.append(metric)
        if len(self._recent_calls) > self._max_traces:
            self._recent_calls = self._recent_calls[-self._max_traces :]

    def get_recent_traces(self, limit: int = 50) -> List[ToolCallMetric]:
        return list(reversed(self._recent_calls[-limit:]))

    def get_metrics_snapshot(self) -> ProxyMetrics:
        return self.metrics.model_copy()
