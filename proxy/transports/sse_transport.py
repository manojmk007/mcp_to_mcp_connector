"""
proxy/transports/sse_transport.py
Server-Sent Events (SSE) transport for downstream MCP servers.

MCP over SSE protocol:
  - GET  /sse          → receive server → client events
  - POST /messages     → send client → server requests

This transport:
  - Maintains a persistent GET SSE stream
  - Posts JSON-RPC requests via HTTP POST
  - Correlates responses by id via pending futures
  - Handles SSE reconnect automatically
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Dict, Optional
from urllib.parse import urljoin

import aiohttp
import structlog

from proxy.transports.base import BaseTransport
from shared.models import ConnectionState

log = structlog.get_logger(__name__)

_SSE_RECONNECT_DELAY = 2.0


class SSETransport(BaseTransport):
    """
    SSE-based MCP transport.

    The MCP-over-SSE protocol works as follows:
      1. Client opens GET /sse → server streams events
      2. Server sends an initial 'endpoint' event containing the POST URL
      3. Client POSTs JSON-RPC messages to that endpoint URL
      4. Server responds via the SSE stream with matching id
    """

    def __init__(
        self,
        downstream_id: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        request_timeout_s: float = 30.0,
        connect_timeout_s: float = 30.0,
    ) -> None:
        super().__init__(downstream_id, url)
        self._headers = {
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
            **(headers or {}),
        }
        self._request_timeout_s = request_timeout_s
        self._connect_timeout_s = connect_timeout_s
        self._session: Optional[aiohttp.ClientSession] = None
        self._sse_task: Optional[asyncio.Task] = None
        self._pending: Dict[str, asyncio.Future] = {}
        self._post_url: Optional[str] = None  # Discovered from 'endpoint' SSE event
        self._endpoint_ready = asyncio.Event()
        self._last_event: str = ""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        await self._set_state(ConnectionState.CONNECTING)
        log.info("sse_connecting", downstream=self.downstream_id, url=self.url)

        connector = aiohttp.TCPConnector(limit=10, ttl_dns_cache=300)
        timeout = aiohttp.ClientTimeout(
            total=None,  # No total timeout for SSE streams
            connect=self._connect_timeout_s,
        )
        self._session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers={k: v for k, v in self._headers.items() if k != "Accept"},
        )

        self._endpoint_ready.clear()
        self._sse_task = asyncio.create_task(
            self._sse_receive_loop(), name=f"sse-recv-{self.downstream_id}"
        )

        # Wait for the SSE stream to provide the post endpoint
        try:
            await asyncio.wait_for(self._endpoint_ready.wait(), timeout=self._connect_timeout_s)
            await self._set_state(ConnectionState.CONNECTED)
            log.info("sse_connected", downstream=self.downstream_id, post_url=self._post_url)
        except asyncio.TimeoutError:
            await self._set_state(ConnectionState.FAILED)
            if self._sse_task and not self._sse_task.done():
                self._sse_task.cancel()
            raise ConnectionError(
                f"[{self.downstream_id}] SSE connect timeout: no endpoint event received"
            )

    async def disconnect(self) -> None:
        await self._set_state(ConnectionState.DISCONNECTED)
        log.info("sse_disconnecting", downstream=self.downstream_id)

        if self._sse_task and not self._sse_task.done():
            self._sse_task.cancel()
            try:
                await self._sse_task
            except asyncio.CancelledError:
                pass

        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError("SSE transport disconnected"))
        self._pending.clear()

    # ------------------------------------------------------------------
    # SSE receive loop (background task)
    # ------------------------------------------------------------------

    async def _sse_receive_loop(self) -> None:
        """
        Maintain a persistent GET connection to the SSE endpoint.
        Parse SSE events and dispatch responses to pending futures.
        """
        retry_delay = _SSE_RECONNECT_DELAY
        while True:
            try:
                async with self._session.get(  # type: ignore[union-attr]
                    self.url,
                    headers={"Accept": "text/event-stream"},
                ) as response:
                    if response.status != 200:
                        log.error(
                            "sse_bad_status",
                            downstream=self.downstream_id,
                            status=response.status,
                        )
                        await asyncio.sleep(retry_delay)
                        continue

                    await self._set_state(ConnectionState.CONNECTED)
                    retry_delay = _SSE_RECONNECT_DELAY  # reset on success

                    while not response.content.at_eof():
                        log.debug("sse_reading_line", downstream=self.downstream_id)
                        line_bytes = await response.content.readline()
                        log.debug("sse_read_line_done", downstream=self.downstream_id, length=len(line_bytes))
                        if not line_bytes:
                            break
                        line = line_bytes.decode("utf-8").rstrip("\n\r")
                        await self._process_sse_line(line)

            except asyncio.CancelledError:
                break
            except (aiohttp.ClientError, OSError) as exc:
                log.warning(
                    "sse_stream_error",
                    downstream=self.downstream_id,
                    error=str(exc),
                    retry_in=retry_delay,
                )
                await self._set_state(ConnectionState.RECONNECTING)
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60.0)

    async def _process_sse_line(self, line: str) -> None:
        """Parse a single SSE line and process the event if complete."""
        try:
            log.debug("sse_line_raw", downstream=self.downstream_id, line=line)
            if not line:
                return

            if line.startswith("event:"):
                event_name = line[6:].strip()
                self._last_event = event_name
                log.debug("sse_event_type", downstream=self.downstream_id, event_type=event_name)
                return

            if line.startswith("data:"):
                raw = line[5:].strip()
                if not raw:
                    return

                log.debug("sse_data_received", downstream=self.downstream_id, data=raw[:100])

                # The MCP-over-SSE 'endpoint' event gives us the POST URL
                is_endpoint = (getattr(self, "_last_event", "") == "endpoint") or (raw.startswith("http") or raw.startswith("/"))
                
                if is_endpoint and (raw.startswith("http") or raw.startswith("/")):
                    self._post_url = raw if raw.startswith("http") else urljoin(self.url, raw)
                    log.info("sse_endpoint_discovered", downstream=self.downstream_id, post_url=self._post_url)
                    self._endpoint_ready.set()
                    return

                data: Dict[str, Any] = json.loads(raw)
                msg_id = str(data.get("id", ""))
                if msg_id and msg_id in self._pending:
                    fut = self._pending.pop(msg_id)
                    if not fut.done():
                        fut.set_result(data)
                else:
                    # Notification or unsolicited
                    log.debug(
                        "sse_unsolicited_message",
                        downstream=self.downstream_id,
                        method=data.get("method"),
                    )
        except Exception as exc:
            log.exception("sse_process_error", downstream=self.downstream_id, error=str(exc))

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def send(self, message: Dict[str, Any]) -> None:
        """POST a JSON-RPC message to the discovered endpoint URL."""
        if not self._post_url:
            raise ConnectionError(f"[{self.downstream_id}] No SSE endpoint URL discovered yet")
        if not self._session or self._session.closed:
            raise ConnectionError(f"[{self.downstream_id}] HTTP session is closed")

        try:
            async with self._session.post(
                self._post_url,
                json=message,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=self._request_timeout_s),
            ) as resp:
                if resp.status not in (200, 202, 204):
                    body = await resp.text()
                    log.warning(
                        "sse_post_bad_status",
                        downstream=self.downstream_id,
                        status=resp.status,
                        body=body[:200],
                    )
        except aiohttp.ClientError as exc:
            log.error("sse_post_error", downstream=self.downstream_id, error=str(exc))
            raise ConnectionError(f"SSE POST failed: {exc}") from exc

    async def receive(self) -> Dict[str, Any]:
        """Not applicable for SSE – use send_and_receive."""
        raise NotImplementedError("SSE transport uses send_and_receive for correlated calls.")

    async def send_and_receive(
        self,
        message: Dict[str, Any],
        timeout_s: float = 30.0,
    ) -> Dict[str, Any]:
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
                "sse_request_timeout",
                downstream=self.downstream_id,
                req_id=req_id,
                timeout_s=timeout_s,
            )
            raise
        except Exception:
            self._pending.pop(req_id, None)
            raise

    async def heartbeat(self) -> bool:
        """Check if the SSE connection is alive by verifying state."""
        return self._state == ConnectionState.CONNECTED

    # ------------------------------------------------------------------
    # SSE-specific: stream responses (for streaming tool calls)
    # ------------------------------------------------------------------

    async def send_streaming(
        self,
        message: Dict[str, Any],
        timeout_s: float = 60.0,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        POST a request and yield partial SSE data events until completion.
        Used for streaming LLM responses in future.
        """
        req_id = str(message.get("id", str(uuid.uuid4())))
        message["id"] = req_id

        # For now, delegate to send_and_receive
        result = await self.send_and_receive(message, timeout_s=timeout_s)
        yield result
