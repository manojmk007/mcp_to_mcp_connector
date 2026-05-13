"""
mcp1/server.py - Custom local MCP Server (MCP1).

Exposes tools: search_docs, summarize_text, ping
Transport: WebSocket (ws://host:port/ws)
Protocol: JSON-RPC 2.0 / MCP 2024-11-05

This server is a DOWNSTREAM MCP server consumed by the proxy.
"""
from __future__ import annotations

import asyncio
import json
import sys
import os

# Ensure project root is in path when running directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import structlog
import websockets

from mcp1.handlers.tool_handler import handle_tools_call, handle_tools_list
from shared.jsonrpc import ErrorCode, MCPMethod, make_error, make_success, validate_jsonrpc
from shared.logging_config import configure_logging

log = structlog.get_logger(__name__)

_SERVER_NAME = "mcp1-local-server"
_SERVER_VERSION = "1.0.0"
_PROTOCOL_VERSION = "2024-11-05"


async def _handle_connection(websocket: Any, path: str = "/ws") -> None:
    """Handle a single WebSocket client connection on MCP1."""
    remote = websocket.remote_address
    log.info("mcp1_client_connected", remote=str(remote))
    _initialized = False

    try:
        async for raw in websocket:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                err = make_error(ErrorCode.PARSE_ERROR, f"JSON parse error: {exc}")
                await websocket.send(err.model_dump_json())
                continue

            err_msg = validate_jsonrpc(data)
            if err_msg:
                err = make_error(ErrorCode.INVALID_REQUEST, err_msg, req_id=data.get("id"))
                await websocket.send(err.model_dump_json())
                continue

            method = data["method"]
            params = data.get("params") or {}
            req_id = data.get("id")

            # ------ Lifecycle ------
            if method == MCPMethod.INITIALIZE:
                client_info = params.get("clientInfo", {})
                log.info(
                    "mcp1_initialize",
                    client=client_info.get("name"),
                    version=params.get("protocolVersion"),
                )
                result = {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": _SERVER_NAME, "version": _SERVER_VERSION},
                }
                _initialized = True
                resp = make_success(result, req_id)
                await websocket.send(resp.model_dump_json())
                continue

            if method == MCPMethod.INITIALIZED:
                log.debug("mcp1_initialized_notification")
                continue  # Notification, no response needed

            if method == MCPMethod.PING:
                resp = make_success({"pong": True}, req_id)
                await websocket.send(resp.model_dump_json())
                continue

            if not _initialized:
                err = make_error(
                    ErrorCode.INVALID_REQUEST,
                    "Server not initialized – send initialize first",
                    req_id=req_id,
                )
                await websocket.send(err.model_dump_json())
                continue

            # ------ Tools ------
            if method == MCPMethod.TOOLS_LIST:
                result = await handle_tools_list()
                resp = make_success(result, req_id)
                await websocket.send(resp.model_dump_json())
                continue

            if method == MCPMethod.TOOLS_CALL:
                response = await handle_tools_call(params, req_id)
                await websocket.send(json.dumps(response))
                continue

            # ------ Unknown ------
            err = make_error(
                ErrorCode.METHOD_NOT_FOUND,
                f"Method not found: {method}",
                req_id=req_id,
            )
            await websocket.send(err.model_dump_json())

    except websockets.exceptions.ConnectionClosed:
        pass
    except Exception as exc:
        log.exception("mcp1_connection_error", error=str(exc))
    finally:
        log.info("mcp1_client_disconnected", remote=str(remote))


async def main(host: str = "0.0.0.0", port: int = 9001) -> None:
    configure_logging(level="INFO")
    log.info("mcp1_starting", host=host, port=port)

    async with websockets.serve(
        _handle_connection,
        host,
        port,
        ping_interval=20,
        ping_timeout=10,
    ):
        log.info("mcp1_ready", url=f"ws://{host}:{port}/ws")
        await asyncio.Future()  # Run forever


if __name__ == "__main__":
    import argparse
    from typing import Any

    parser = argparse.ArgumentParser(description="MCP1 Local Server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9001)
    args = parser.parse_args()

    asyncio.run(main(args.host, args.port))
