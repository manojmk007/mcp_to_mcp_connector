"""
examples/example_client.py - Example MCP client that connects to the proxy.

Demonstrates:
  1. Initialize handshake
  2. tools/list discovery
  3. tools/call for each available tool
  4. Pretty-printed results

Usage:
    python examples/example_client.py
    python examples/example_client.py --transport http
    python examples/example_client.py --transport sse
"""
from __future__ import annotations

import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp
import websockets

PROXY_WS_URL = "ws://localhost:8765/ws"
PROXY_HTTP_URL = "http://localhost:8088/mcp"
PROXY_SSE_URL = "http://localhost:8088/sse"


# ---------------------------------------------------------------------------
# WebSocket client
# ---------------------------------------------------------------------------

async def ws_client_demo() -> None:
    print("\n" + "="*60)
    print("  MCP Proxy Gateway — WebSocket Client Demo")
    print("="*60)

    async with websockets.connect(PROXY_WS_URL) as ws:

        async def rpc(method: str, params: dict = None) -> dict:
            req = {"jsonrpc": "2.0", "method": method, "params": params or {}, "id": str(id(params))}
            await ws.send(json.dumps(req))
            raw = await ws.recv()
            return json.loads(raw)

        # 1. Initialize
        print("\n[INFO] Sending initialize...")
        resp = await rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "clientInfo": {"name": "example-client", "version": "1.0"},
            "capabilities": {},
        })
        server_info = resp.get("result", {}).get("serverInfo", {})
        print(f"   OK: Connected to: {server_info.get('name')} v{server_info.get('version')}")

        # 2. List tools
        print("\n[TOOLS] Fetching tool list...")
        resp = await rpc("tools/list")
        tools = resp.get("result", {}).get("tools", [])
        print(f"   -> Found {len(tools)} tools:")
        for tool in tools:
            print(f"      • {tool['name']}: {tool.get('description', '(no description)')[:60]}")

        # 3. Call specific tools
        demo_calls = [
            ("ping", {}),
            ("search_docs", {"query": "MCP protocol architecture"}),
            ("summarize_text", {
                "text": (
                    "The Model Context Protocol enables tool integration. "
                    "It uses JSON-RPC 2.0 for communication. "
                    "Circuit breakers improve reliability. "
                    "WebSocket provides real-time communication. "
                    "The proxy aggregates multiple MCP servers behind one interface."
                ),
                "max_sentences": 2,
            }),
        ]

        for tool_name, arguments in demo_calls:
            # Check if tool is available
            available = any(t["name"] == tool_name for t in tools)
            if not available:
                print(f"\n⚠️  Tool '{tool_name}' not available (downstream may not be connected)")
                continue

            print(f"\n[CALL] Calling tool: {tool_name}")
            print(f"   Args: {json.dumps(arguments, indent=2)[:150]}")

            resp = await rpc("tools/call", {"name": tool_name, "arguments": arguments})
            if resp.get("error"):
                print(f"   ❌ Error: {resp['error']}")
            else:
                content = resp.get("result", {}).get("content", [])
                for item in content:
                    text = item.get("text", "")
                    print(f"   OK: Result: {text[:300]}")

        print("\n" + "="*60)
        print("  Demo complete!")
        print("="*60 + "\n")


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

async def http_client_demo() -> None:
    print("\n" + "="*60)
    print("  MCP Proxy Gateway — HTTP Client Demo")
    print("="*60)

    async with aiohttp.ClientSession() as session:

        async def rpc(method: str, params: dict = None) -> dict:
            req = {"jsonrpc": "2.0", "method": method, "params": params or {}, "id": "1"}
            async with session.post(PROXY_HTTP_URL, json=req) as resp:
                return await resp.json()

        print("\n[INFO] Sending initialize...")
        resp = await rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "clientInfo": {"name": "http-example-client", "version": "1.0"},
            "capabilities": {},
        })
        server_info = resp.get("result", {}).get("serverInfo", {})
        print(f"   OK: Connected to: {server_info.get('name')}")

        print("\n[TOOLS] Fetching tool list...")
        resp = await rpc("tools/list")
        tools = resp.get("result", {}).get("tools", [])
        print(f"   -> Found {len(tools)} tools: {[t['name'] for t in tools]}")

        # Call ping if available
        if any(t["name"] == "ping" for t in tools):
            print("\n[CALL] Calling ping...")
            resp = await rpc("tools/call", {"name": "ping", "arguments": {}})
            content = resp.get("result", {}).get("content", [])
            for item in content:
                print(f"   OK: {item.get('text', '')}")

    print()


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

async def health_check() -> None:
    async with aiohttp.ClientSession() as session:
        async with session.get("http://localhost:8088/health") as resp:
            data = await resp.json()
            print("\n[HEALTH] Health Check:")
            print(f"   Status: {data.get('status')}")
            print(f"   Tools:  {data.get('tool_count')}")
            print(f"   Uptime: {data.get('uptime_s', 0):.1f}s")
            for ds in data.get("downstream_health", []):
                icon = "OK:" if ds.get("state") == "connected" else "ERR:"
                print(f"   {icon} {ds['name']}: {ds['state']} ({ds['tool_count']} tools)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=["ws", "http", "health"], default="ws")
    args = parser.parse_args()

    if args.transport == "ws":
        await ws_client_demo()
    elif args.transport == "http":
        await http_client_demo()
    elif args.transport == "health":
        await health_check()


if __name__ == "__main__":
    asyncio.run(main())
