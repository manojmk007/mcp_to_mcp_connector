"""
proxy/transports/base.py - Abstract base class for all MCP transport implementations.
All concrete transports (WebSocket, SSE) must implement this interface.
"""
from __future__ import annotations

import abc
import asyncio
from typing import Any, AsyncIterator, Dict, Optional

import structlog

from shared.models import ConnectionState

log = structlog.get_logger(__name__)


class BaseTransport(abc.ABC):
    """
    Abstract transport that every downstream connector must implement.

    Lifecycle:
        connect() → send()/receive() loop → disconnect()

    Reconnect is handled externally by the session manager.
    """

    def __init__(self, downstream_id: str, url: str) -> None:
        self.downstream_id = downstream_id
        self.url = url
        self._state: ConnectionState = ConnectionState.DISCONNECTED
        self._state_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    @property
    def state(self) -> ConnectionState:
        return self._state

    async def _set_state(self, state: ConnectionState) -> None:
        async with self._state_lock:
            self._state = state
            log.debug("transport_state_changed", downstream=self.downstream_id, state=state.value)

    @property
    def is_connected(self) -> bool:
        return self._state == ConnectionState.CONNECTED

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abc.abstractmethod
    async def connect(self) -> None:
        """Establish connection. Must set state to CONNECTED on success."""
        ...

    @abc.abstractmethod
    async def disconnect(self) -> None:
        """Gracefully close the connection."""
        ...

    @abc.abstractmethod
    async def send(self, message: Dict[str, Any]) -> None:
        """Send a JSON-serialisable message to the downstream server."""
        ...

    @abc.abstractmethod
    async def receive(self) -> Dict[str, Any]:
        """
        Block until a complete JSON message arrives from the downstream server.
        Should raise ConnectionError or asyncio.CancelledError on disconnect.
        """
        ...

    @abc.abstractmethod
    async def send_and_receive(
        self,
        message: Dict[str, Any],
        timeout_s: float = 30.0,
    ) -> Dict[str, Any]:
        """
        Send a request and await the correlated response.
        Higher-level transport implementations track in-flight requests by id.
        """
        ...

    @abc.abstractmethod
    async def heartbeat(self) -> bool:
        """
        Send a ping/heartbeat.
        Returns True if the connection is alive, False otherwise.
        """
        ...

    # ------------------------------------------------------------------
    # Optional stream-based receive (for SSE style transports)
    # ------------------------------------------------------------------

    async def stream_receive(self) -> AsyncIterator[Dict[str, Any]]:
        """
        Optional: iterate over incoming messages as an async generator.
        Default implementation loops over receive().
        """
        while True:
            try:
                msg = await self.receive()
                yield msg
            except (ConnectionError, asyncio.CancelledError):
                break
