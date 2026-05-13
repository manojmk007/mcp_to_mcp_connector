"""
mcp1/handlers/tool_handler.py - JSON-RPC tool dispatch for MCP1 server.
Routes tools/call requests to the correct tool implementation.
"""
from __future__ import annotations

import time
from typing import Any, Dict

import os
import aiohttp
import structlog
from mcp1.tools.search_docs import search_docs
from mcp1.tools.summarize_text import summarize_text
from shared.jsonrpc import ErrorCode, make_error, make_success

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Tool definitions (sent in response to tools/list)
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "name": "search_docs",
        "description": "Search the documentation corpus for relevant documents matching a query.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query string",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return (default: 3)",
                    "default": 3,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "summarize_text",
        "description": "Produce an extractive summary of a given text block.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The text to summarize",
                },
                "max_sentences": {
                    "type": "integer",
                    "description": "Maximum number of sentences in summary (default: 3)",
                    "default": 3,
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "ping",
        "description": "Health check tool – returns 'pong' with server timestamp.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "delegate_ai_search",
        "description": "Calls MCP2's ai_search tool via the Unified Proxy Gateway.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The query to pass to MCP2's ai_search",
                },
            },
            "required": ["query"],
        },
    },
]


async def handle_tools_list() -> Dict[str, Any]:
    """Return all tool definitions."""
    return {"tools": TOOL_DEFINITIONS}


async def handle_tools_call(params: Dict[str, Any], req_id: Any) -> Dict[str, Any]:
    """Dispatch a tools/call request to the appropriate tool function."""
    tool_name = params.get("name")
    arguments = params.get("arguments", {})

    log.info("mcp1_tool_call", tool=tool_name, arguments=str(arguments)[:200])

    try:
        if tool_name == "search_docs":
            query = arguments.get("query", "")
            top_k = int(arguments.get("top_k", 3))
            results = await search_docs(query=query, top_k=top_k)
            content = [{"type": "text", "text": str(results)}]

        elif tool_name == "summarize_text":
            text = arguments.get("text", "")
            max_sentences = int(arguments.get("max_sentences", 3))
            summary = await summarize_text(text=text, max_sentences=max_sentences)
            content = [{"type": "text", "text": summary}]

        elif tool_name == "ping":
            content = [{"type": "text", "text": f"pong – mcp1 server time: {time.time():.3f}"}]

        elif tool_name == "delegate_ai_search":
            query = arguments.get("query", "")
            proxy_url = os.getenv("PROXY_URL", "http://localhost:8088/mcp")
            
            payload = {
                "jsonrpc": "2.0",
                "id": "mcp1-delegate",
                "method": "tools/call",
                "params": {
                    "name": "ai_search",
                    "arguments": {"query": query}
                }
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(proxy_url, json=payload) as resp:
                    data = await resp.json()
                    log.info("mcp1_proxy_response", data=data)
                    if data.get("error"):
                        text = f"Error from proxy: {data['error']}"
                    else:
                        result = data.get("result", {})
                        content_list = result.get("content", [])
                        text = "\n".join([c.get("text", "") for c in content_list])
                    content = [{"type": "text", "text": text}]

        else:
            return make_error(
                ErrorCode.TOOL_NOT_FOUND,
                f"Unknown tool: {tool_name}",
                req_id=req_id,
            ).model_dump()

        result = {"content": content, "isError": False}
        return make_success(result, req_id).model_dump()

    except Exception as exc:
        log.exception("mcp1_tool_error", tool=tool_name, error=str(exc))
        return make_error(
            ErrorCode.INTERNAL_ERROR,
            f"Tool execution failed: {exc}",
            req_id=req_id,
        ).model_dump()
