"""
main.py - Main entrypoint for the MCP Proxy Gateway.

Starts:
  1. MCP1 local server  (ws://localhost:9001)   [in subprocess]
  2. Proxy server        (ws://localhost:8765 + http://localhost:8080)

Usage:
    python main.py [--no-mcp1]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

import structlog

from proxy.server import MCPProxyServer
from shared.logging_config import configure_logging
from shared.models import DownstreamConfig, TransportType

log = structlog.get_logger(__name__)


def _build_proxy(args: argparse.Namespace) -> MCPProxyServer:
    """Build and configure the proxy server with all downstream connections."""
    proxy = MCPProxyServer(
        host=args.host,
        ws_port=args.ws_port,
        http_port=args.http_port,
        log_level=args.log_level,
        json_logs=args.json_logs,
    )

    # --- MCP1: Local WebSocket server ---
    if not args.no_mcp1:
        mcp1_config = DownstreamConfig(
            id="mcp1",
            name="MCP1 Local Server",
            transport=TransportType.WEBSOCKET,
            url=f"ws://{args.mcp1_host}:{args.mcp1_port}/ws",
            reconnect_interval_s=3.0,
            max_reconnect_attempts=20,
            request_timeout_s=15.0,
            heartbeat_interval_s=10.0,
            cb_failure_threshold=5,
            cb_recovery_timeout_s=30.0,
        )
        proxy.add_downstream(mcp1_config)

    # --- MCP2: Thunai SSE endpoint ---
    thunai_url = os.getenv(
        "THUNAI_MCP_URL",
        "https://api.thunai.ai/mcp-service/thunai/service/mcp-sse/mcp",
    )
    if args.mock_thunai:
        thunai_url = "http://127.0.0.1:9002/sse"
    thunai_config = DownstreamConfig(
        id="thunai",
        name="Thunai MCP",
        transport=TransportType.SSE,
        url=thunai_url,
        reconnect_interval_s=10.0,
        max_reconnect_attempts=10,
        request_timeout_s=30.0,
        heartbeat_interval_s=30.0,
        cb_failure_threshold=3,
        cb_recovery_timeout_s=60.0,
    )
    proxy.add_downstream(thunai_config)

    return proxy


async def _start_mcp1_subprocess() -> asyncio.subprocess.Process:
    """Launch MCP1 server as a subprocess."""
    cmd = [sys.executable, "-m", "mcp1.server"]
    log.info("mcp1_subprocess_starting", cmd=" ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # Give it a moment to start
    await asyncio.sleep(1.5)
    log.info("mcp1_subprocess_started", pid=proc.pid)
    asyncio.create_task(_drain_output(proc.stdout, "mcp1-stdout"))
    asyncio.create_task(_drain_output(proc.stderr, "mcp1-stderr"))
    return proc
async def _start_mcp2_subprocess() -> asyncio.subprocess.Process:
    """Launch Mock MCP2 server as a subprocess."""
    cmd = [sys.executable, "-m", "mcp2.server"]
    log.info("mcp2_subprocess_starting", cmd=" ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await asyncio.sleep(1.5)
    log.info("mcp2_subprocess_started", pid=proc.pid)
    asyncio.create_task(_drain_output(proc.stdout, "mcp2-stdout"))
    asyncio.create_task(_drain_output(proc.stderr, "mcp2-stderr"))
    return proc


async def _drain_output(stream: asyncio.StreamReader, label: str) -> None:
    """Read lines from a stream and log them."""
    try:
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode().strip()
            if text:
                log.info("subprocess_output", label=label, message=text)
    except Exception:
        pass


async def main(args: argparse.Namespace) -> None:
    configure_logging(level=args.log_level, json_output=args.json_logs)

    mcp1_proc = None
    if not args.no_mcp1 and not args.external_mcp1:
        mcp1_proc = await _start_mcp1_subprocess()

    mcp2_proc = None
    if args.mock_thunai:
        mcp2_proc = await _start_mcp2_subprocess()

    proxy = _build_proxy(args)

    try:
        await proxy.start()
        log.info(
            "gateway_running",
            ws=f"ws://{args.host}:{args.ws_port}/ws",
            http=f"http://{args.host}:{args.http_port}",
            health=f"http://{args.host}:{args.http_port}/health",
        )

        # Run until interrupted
        stop_event = asyncio.Event()
        try:
            await stop_event.wait()
        except asyncio.CancelledError:
            pass

    except KeyboardInterrupt:
        log.info("keyboard_interrupt_received")
    finally:
        log.info("shutting_down")
        await proxy.stop()

        if mcp1_proc and mcp1_proc.returncode is None:
            mcp1_proc.terminate()
        
        if mcp2_proc and mcp2_proc.returncode is None:
            mcp2_proc.terminate()

        try:
            if mcp1_proc: await asyncio.wait_for(mcp1_proc.wait(), timeout=5.0)
            if mcp2_proc: await asyncio.wait_for(mcp2_proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            if mcp1_proc: mcp1_proc.kill()
            if mcp2_proc: mcp2_proc.kill()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MCP Proxy Gateway")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    parser.add_argument("--ws-port", type=int, default=8765, help="WebSocket port")
    parser.add_argument("--http-port", type=int, default=8088, help="HTTP/SSE port")
    parser.add_argument("--mcp1-host", default="127.0.0.1", help="MCP1 server host")
    parser.add_argument("--mcp1-port", type=int, default=9001, help="MCP1 server port")
    parser.add_argument("--no-mcp1", action="store_true", help="Skip MCP1 connection")
    parser.add_argument(
        "--external-mcp1",
        action="store_true",
        help="MCP1 is already running externally (don't launch subprocess)",
    )
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--json-logs", action="store_true", help="Emit JSON log lines")
    parser.add_argument("--mock-thunai", action="store_true", help="Start a local mock MCP2 (SSE) instead of calling Thunai")

    args = parser.parse_args()

    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        pass
