"""
shared/utils.py - Utility functions: retry logic, timing, circuit breaker, tracing.
"""
from __future__ import annotations

import asyncio
import functools
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Callable, Optional, Type, Tuple

import structlog

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Timing helpers
# ---------------------------------------------------------------------------

class Timer:
    """Simple context manager for measuring elapsed time in milliseconds."""

    def __init__(self) -> None:
        self._start: float = 0.0
        self.elapsed_ms: float = 0.0

    def __enter__(self) -> "Timer":
        self._start = time.perf_counter()
        return self

    def __exit__(self, *_: Any) -> None:
        self.elapsed_ms = (time.perf_counter() - self._start) * 1000


@asynccontextmanager
async def async_timer() -> AsyncGenerator["Timer", None]:
    t = Timer()
    t._start = time.perf_counter()
    try:
        yield t
    finally:
        t.elapsed_ms = (time.perf_counter() - t._start) * 1000


# ---------------------------------------------------------------------------
# Retry decorator / helper
# ---------------------------------------------------------------------------

async def retry_async(
    coro_fn: Callable,
    *args: Any,
    max_attempts: int = 3,
    base_delay_s: float = 1.0,
    max_delay_s: float = 30.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    **kwargs: Any,
) -> Any:
    """
    Retry an async coroutine with exponential back-off.
    Raises the last exception after all attempts are exhausted.
    """
    delay = base_delay_s
    last_exc: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        try:
            return await coro_fn(*args, **kwargs)
        except exceptions as exc:
            last_exc = exc
            if attempt == max_attempts:
                break
            wait = min(delay * (2 ** (attempt - 1)), max_delay_s)
            log.warning(
                "retry_scheduled",
                attempt=attempt,
                max_attempts=max_attempts,
                wait_s=wait,
                error=str(exc),
            )
            await asyncio.sleep(wait)

    raise last_exc  # type: ignore[misc]


def with_retry(
    max_attempts: int = 3,
    base_delay_s: float = 1.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
):
    """Decorator that adds retry logic to an async function."""
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            return await retry_async(
                fn,
                *args,
                max_attempts=max_attempts,
                base_delay_s=base_delay_s,
                exceptions=exceptions,
                **kwargs,
            )
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------

class CircuitBreaker:
    """
    Three-state circuit breaker: CLOSED → OPEN → HALF_OPEN → CLOSED.

    Parameters
    ----------
    failure_threshold : int
        Number of consecutive failures before opening the circuit.
    recovery_timeout_s : float
        Seconds to wait in OPEN state before attempting a probe (HALF_OPEN).
    name : str
        Identifier used in log messages.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout_s: float = 30.0,
        name: str = "circuit",
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout_s = recovery_timeout_s
        self._failures = 0
        self._state = "closed"  # closed | open | half_open
        self._opened_at: Optional[float] = None

    @property
    def state(self) -> str:
        return self._state

    @property
    def is_open(self) -> bool:
        if self._state == "open":
            # Check if recovery timeout has elapsed
            if self._opened_at and (time.time() - self._opened_at) >= self.recovery_timeout_s:
                self._state = "half_open"
                log.info("circuit_half_open", name=self.name)
                return False
            return True
        return False

    def record_success(self) -> None:
        if self._state in ("half_open", "open"):
            log.info("circuit_closed", name=self.name)
        self._failures = 0
        self._state = "closed"
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._state == "half_open" or self._failures >= self.failure_threshold:
            self._state = "open"
            self._opened_at = time.time()
            log.warning(
                "circuit_opened",
                name=self.name,
                failures=self._failures,
            )

    async def call(self, coro_fn: Callable, *args: Any, **kwargs: Any) -> Any:
        if self.is_open:
            from shared.jsonrpc import ErrorCode
            raise RuntimeError(
                f"[CircuitBreaker:{self.name}] Circuit is OPEN – rejecting request"
            )
        try:
            result = await coro_fn(*args, **kwargs)
            self.record_success()
            return result
        except Exception as exc:
            self.record_failure()
            raise

    def __repr__(self) -> str:
        return f"<CircuitBreaker name={self.name!r} state={self._state} failures={self._failures}>"


# ---------------------------------------------------------------------------
# Trace ID helpers
# ---------------------------------------------------------------------------

def new_trace_id() -> str:
    return str(uuid.uuid4())


def truncate(text: str, max_len: int = 200) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "…"


# ---------------------------------------------------------------------------
# Async timeout helper
# ---------------------------------------------------------------------------

async def with_timeout(coro: Any, timeout_s: float, label: str = "operation") -> Any:
    """
    Await a coroutine with a timeout. Raises asyncio.TimeoutError on expiry.
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout_s)
    except asyncio.TimeoutError:
        log.error("timeout", label=label, timeout_s=timeout_s)
        raise


# ---------------------------------------------------------------------------
# Graceful shutdown helpers
# ---------------------------------------------------------------------------

async def run_with_graceful_shutdown(
    main_coro: Any,
    shutdown_coro: Optional[Any] = None,
) -> None:
    """Run main_coro and invoke shutdown_coro on SIGINT/SIGTERM."""
    import signal

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        log.info("shutdown_signal_received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows does not support add_signal_handler for all signals
            pass

    main_task = asyncio.create_task(main_coro)

    await stop_event.wait()
    log.info("graceful_shutdown_starting")
    main_task.cancel()
    try:
        await main_task
    except asyncio.CancelledError:
        pass

    if shutdown_coro is not None:
        await shutdown_coro
    log.info("graceful_shutdown_complete")
