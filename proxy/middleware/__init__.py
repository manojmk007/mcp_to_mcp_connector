"""
proxy/middleware/__init__.py - Middleware pipeline for the proxy server.

Middleware is applied to every incoming JSON-RPC request, in order:
  1. RequestLoggingMiddleware  - structured request/response log
  2. TracingMiddleware         - bind trace_id to context
  3. MetricsMiddleware         - count requests, record timing
  4. RateLimitMiddleware       - (stub) future rate limiting hook
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

import structlog

log = structlog.get_logger(__name__)

# Type alias for the next handler in the chain
NextHandler = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]


class BaseMiddleware:
    async def __call__(
        self, request: Dict[str, Any], next_handler: NextHandler
    ) -> Dict[str, Any]:
        return await next_handler(request)


class RequestLoggingMiddleware(BaseMiddleware):
    """Log every request and response with structured fields."""

    async def __call__(
        self, request: Dict[str, Any], next_handler: NextHandler
    ) -> Dict[str, Any]:
        method = request.get("method", "<unknown>")
        req_id = request.get("id")
        log.info("request_received", method=method, req_id=req_id)

        start = time.perf_counter()
        response = await next_handler(request)
        elapsed_ms = (time.perf_counter() - start) * 1000

        has_error = bool(response.get("error"))
        log.info(
            "request_completed",
            method=method,
            req_id=req_id,
            latency_ms=round(elapsed_ms, 2),
            has_error=has_error,
        )
        return response


class TracingMiddleware(BaseMiddleware):
    """Attach a trace_id to the structlog context for each request."""

    async def __call__(
        self, request: Dict[str, Any], next_handler: NextHandler
    ) -> Dict[str, Any]:
        trace_id = request.get("params", {}).get("_trace_id") if request.get("params") else None
        if not trace_id:
            trace_id = str(uuid.uuid4())

        structlog.contextvars.bind_contextvars(trace_id=trace_id)
        try:
            return await next_handler(request)
        finally:
            structlog.contextvars.unbind_contextvars("trace_id")


class MetricsMiddleware(BaseMiddleware):
    """
    Collect basic per-method metrics.
    Hooks into an external metrics store (stub – ready for Prometheus).
    """

    def __init__(self) -> None:
        self._counts: Dict[str, int] = {}
        self._errors: Dict[str, int] = {}
        self._latencies: Dict[str, float] = {}

    async def __call__(
        self, request: Dict[str, Any], next_handler: NextHandler
    ) -> Dict[str, Any]:
        method = request.get("method", "<unknown>")
        start = time.perf_counter()

        response = await next_handler(request)

        elapsed_ms = (time.perf_counter() - start) * 1000
        self._counts[method] = self._counts.get(method, 0) + 1
        self._latencies[method] = self._latencies.get(method, 0.0) + elapsed_ms
        if response.get("error"):
            self._errors[method] = self._errors.get(method, 0) + 1

        return response

    def snapshot(self) -> Dict[str, Any]:
        return {
            "counts": dict(self._counts),
            "errors": dict(self._errors),
            "total_latency_ms": dict(self._latencies),
        }


class MiddlewarePipeline:
    """
    Compose a list of middleware into a single callable pipeline.
    Execution order: first middleware wraps last.
    """

    def __init__(self, middlewares: Optional[List[BaseMiddleware]] = None) -> None:
        self._middlewares: List[BaseMiddleware] = middlewares or []

    def add(self, middleware: BaseMiddleware) -> None:
        self._middlewares.append(middleware)

    async def execute(
        self,
        request: Dict[str, Any],
        final_handler: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """Run the middleware chain and call final_handler at the end."""

        async def build_chain(index: int) -> Callable:
            if index >= len(self._middlewares):
                return final_handler

            middleware = self._middlewares[index]
            next_fn = await build_chain(index + 1)

            async def call(req: Dict[str, Any]) -> Dict[str, Any]:
                return await middleware(req, next_fn)

            return call

        chain = await build_chain(0)
        return await chain(request)
