import asyncio
import json
import uuid
import os
from typing import Any, Dict, List
from aiohttp import web
import structlog

log = structlog.get_logger(__name__)

# Track active SSE sessions
sessions: Dict[str, asyncio.Queue] = {}

# The Proxy Gateway URL (hardcoded for this demo, usually from env)
PROXY_URL = os.environ.get("PROXY_URL", "http://localhost:8088/mcp")

async def handle_sse(request: web.Request):
    session_id = str(uuid.uuid4())
    log.info("mcp2_sse_client_connected", session_id=session_id)
    
    response = web.StreamResponse(headers={
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
    })
    await response.prepare(request)
    
    # Register session
    queue = asyncio.Queue()
    sessions[session_id] = queue
    
    try:
        # 1. Send the endpoint event
        post_url = f"http://{request.host}/messages?session={session_id}"
        log.info("mcp2_sending_endpoint", url=post_url)
        await response.write(f"event: endpoint\ndata: {post_url}\n\n".encode())
        
        # 2. Loop and push messages from the queue
        while True:
            try:
                # Wait for a message or timeout for keepalive
                msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                await response.write(f"data: {json.dumps(msg)}\n\n".encode())
            except asyncio.TimeoutError:
                await response.write(b": keepalive\n\n")
    except Exception:
        log.info("mcp2_sse_client_disconnected", session_id=session_id)
    finally:
        sessions.pop(session_id, None)
    return response

async def handle_post(request: web.Request):
    session_id = request.query.get("session")
    if not session_id or session_id not in sessions:
        log.warning("mcp2_session_not_found", session_id=session_id)
        return web.Response(status=404, text="Session not found")
    
    try:
        data = await request.json()
    except:
        return web.Response(status=400)
    
    method = data.get("method")
    req_id = data.get("id")
    
    log.info("mcp2_request_received", method=method, id=req_id)
    
    result: Dict[str, Any] = {}
    
    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mock-mcp2-thunai", "version": "1.0.0"}
        }
    elif method == "tools/list":
        result = {
            "tools": [
                {
                    "name": "ai_search",
                    "description": "Powerful AI-driven search from MCP2 (Mock Thunai)",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "count": {"type": "number", "default": 5}
                        },
                        "required": ["query"]
                    }
                },
                {
                    "name": "summarize_via_mcp1",
                    "description": "Call MCP1's summarization tool from MCP2",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"}
                        },
                        "required": ["text"]
                    }
                }
            ]
        }
    elif method == "tools/call":
        params = data.get("params", {})
        tool_name = params.get("name")
        args = params.get("arguments", {})
        
        if tool_name == "ai_search":
            query = args.get("query", "")
            result = {
                "content": [
                    {
                        "type": "text",
                        "text": f"MOCK AI SEARCH RESULTS for '{query}':\n1. Result from Mock MCP2\nSource: Mock Thunai"
                    }
                ]
            }
        elif tool_name == "summarize_via_mcp1":
            text_to_summarize = args.get("text", "")
            
            # DELEGATION: Call MCP1 via Proxy
            payload = {
                "jsonrpc": "2.0",
                "id": "mcp2-delegate-to-mcp1",
                "method": "tools/call",
                "params": {
                    "name": "summarize_text",
                    "arguments": {"text": text_to_summarize}
                }
            }
            
            log.info("mcp2_delegating_to_mcp1", tool="summarize_text")
            async with aiohttp.ClientSession() as session:
                async with session.post(PROXY_URL, json=payload) as resp:
                    resp_data = await resp.json()
                    
                    if resp_data.get("error"):
                        summary_text = f"Error from Proxy: {resp_data['error']}"
                    else:
                        mcp_res = resp_data.get("result", {})
                        content = mcp_res.get("content", [])
                        summary_text = content[0].get("text", "No result") if content else "No content"
            
            result = {
                "content": [
                    {
                        "type": "text",
                        "text": f"MCP2 DELEGATION RESULT (via MCP1):\n{summary_text}"
                    }
                ]
            }
        else:
            return web.json_response({"jsonrpc": "2.0", "error": {"code": -32601, "message": "Method not found"}, "id": req_id})

    response_data = {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": result
    }
    
    # PUSH to the SSE stream
    await sessions[session_id].put(response_data)
    
    return web.Response(status=202) # Accepted

async def main():
    app = web.Application()
    app.router.add_get("/sse", handle_sse)
    app.router.add_post("/messages", handle_post)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 9002)
    await site.start()
    print("Mock MCP2 (SSE) started on http://127.0.0.1:9002/sse")
    
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    from shared.logging_config import configure_logging
    configure_logging(level="INFO")
    
    # Import aiohttp here to avoid circular or early import issues in main
    import aiohttp
    asyncio.run(main())
