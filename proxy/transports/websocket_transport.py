"""
proxy/transports/websocket_transport.py
Full-duplex WebSocket transport for downstream MCP servers.

Features:
- Correlated request/response via pending futures map
- Concurrent send/receive loop
- Automatic heartbeat (ping frames)
- Graceful disconnect with connection draining
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Dict, Optional

import structlog
import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from proxy.transports.base import BaseTransport
from shared.models import ConnectionState

log = structlog.get_logger(__name__)


class WebSocketTransport(BaseTransport):
    """
    WebSocket transport implementing request/response correlation via
    an in-flight futures dictionary keyed by JSON-RPC request id.
    """

    def __init__(
        self,
        downstream_id: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        ping_interval: float = 20.0,
        ping_timeout: float = 10.0,
        max_message_size: int = 10 * 1024 * 1024,  # 10 MB
    ) -> None:
        super().__init__(downstream_id, url)
        self._headers = headers or {}
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout
        self._max_message_size = max_message_size
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._pending: Dict[str, asyncio.Future] = {}
        self._recv_task: Optional[asyncio.Task] = None
        self._send_queue: asyncio.Queue = asyncio.Queue()
        self._send_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        await self._set_state(ConnectionState.CONNECTING)
        log.info("ws_connecting", downstream=self.downstream_id, url=self.url)
        try:
            self._ws = await websockets.connect(
                self.url,
                additional_headers=self._headers,
                ping_interval=self._ping_interval,
                ping_timeout=self._ping_timeout,
                max_size=self._max_message_size,
            )
            await self._set_state(ConnectionState.CONNECTED)
            log.info("ws_connected", downstream=self.downstream_id)

            # Start background receiver and sender tasks
            self._recv_task = asyncio.create_task(
                self._receive_loop(), name=f"ws-recv-{self.downstream_id}"
            )
            self._send_task = asyncio.create_task(
                self._send_loop(), name=f"ws-send-{self.downstream_id}"
            )

        except (ConnectionRefusedError, OSError, WebSocketException) as exc:
            await self._set_state(ConnectionState.FAILED)
            log.error("ws_connect_failed", downstream=self.downstream_id, error=str(exc))
            raise ConnectionError(f"WebSocket connect failed: {exc}") from exc

    async def disconnect(self) -> None:
        await self._set_state(ConnectionState.DISCONNECTED)
        log.info("ws_disconnecting", downstream=self.downstream_id)

        for task in (self._recv_task, self._send_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        if self._ws:
            closed = getattr(self._ws, "closed", True)
            if not closed:
                try:
                    await self._ws.close()
                except Exception:
                    pass
        self._ws = None

        # Fail all pending futures
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError("WebSocket disconnected"))
        self._pending.clear()

    # ------------------------------------------------------------------
    # Send / Receive loops (background tasks)
    # ------------------------------------------------------------------

    async def _send_loop(self) -> None:
        """Drain the send queue and write frames to the WebSocket."""
        try:
            while True:
                payload = await self._send_queue.get()
                if self._ws is None:
                    log.warning("ws_send_dropped_no_connection", downstream=self.downstream_id)
                    continue
                try:
                    await self._ws.send(payload)
                except (ConnectionClosed, WebSocketException) as exc:
                    log.error("ws_send_error", downstream=self.downstream_id, error=str(exc))
                    await self._set_state(ConnectionState.DISCONNECTED)
                    # Signal all pending as failed
                    for fut in self._pending.values():
                        if not fut.done():
                            fut.set_exception(exc)
                    self._pending.clear()
                    break
        except asyncio.CancelledError:
            pass

    async def _receive_loop(self) -> None:
        """Continuously read frames and resolve pending futures."""
        try:
            async for raw in self._ws:  # type: ignore[union-attr]
                try:
                    data: Dict[str, Any] = json.loads(raw)
                except json.JSONDecodeError:
                    log.warning("ws_invalid_json", downstream=self.downstream_id, raw=str(raw)[:200])
                    continue

                msg_id = str(data.get("id", ""))
                if msg_id and msg_id in self._pending:
                    fut = self._pending.pop(msg_id)
                    if not fut.done():
                        fut.set_result(data)
                else:
                    # Notification or unsolicited – log and discard for now
                    log.debug(
                        "ws_unsolicited_message",
                        downstream=self.downstream_id,
                        method=data.get("method"),
                    )
        except (ConnectionClosed, WebSocketException) as exc:
            log.warning("ws_receive_loop_closed", downstream=self.downstream_id, error=str(exc))
            await self._set_state(ConnectionState.DISCONNECTED)
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(exc)
            self._pending.clear()
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def send(self, message: Dict[str, Any]) -> None:
        payload = json.dumps(message)
        await self._send_queue.put(payload)

    async def receive(self) -> Dict[str, Any]:
        """
        Not used directly – the receive loop handles incoming messages.
        Raises NotImplementedError to signal callers to use send_and_receive.
        """
        raise NotImplementedError("Use send_and_receive for correlated calls.")

    async def send_and_receive(
        self,
        message: Dict[str, Any],
        timeout_s: float = 30.0,
    ) -> Dict[str, Any]:
        """
        Send a request and wait for the correlated response (matched by id).
        """
        if not self.is_connected:
            raise ConnectionError(f"[{self.downstream_id}] Not connected (state={self._state})")

        req_id = str(message.get("id", str(uuid.uuid4())))
        message["id"] = req_id

        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[req_id] = fut

        try:
            await self.send(message)
            return await asyncio.wait_for(fut, timeout=timeout_s)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            log.error(
                "ws_request_timeout",
                downstream=self.downstream_id,
                req_id=req_id,
                timeout_s=timeout_s,
            )
            raise
        except Exception:
            self._pending.pop(req_id, None)
            raise

    async def heartbeat(self) -> bool:
        """Send a WebSocket ping frame and check for pong."""
        if not self._ws:
            return False
        
        # Check if open/closed attributes exist and use them if they do
        is_closed = getattr(self._ws, "closed", None)
        if is_closed is True:
            return False
            
        is_open = getattr(self._ws, "open", None)
        if is_open is False:
            return False

        try:
            # ping() is standard for websockets
            pong = await self._ws.ping()
            await asyncio.wait_for(pong, timeout=10.0)
            return True
        except Exception as exc:
            log.warning("ws_heartbeat_failed", downstream=self.downstream_id, error=str(exc))
            return False
