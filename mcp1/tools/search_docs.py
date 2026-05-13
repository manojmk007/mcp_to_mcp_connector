"""
mcp1/tools/search_docs.py - search_docs tool implementation.
Simulates a document search over a static corpus.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List


# Fake document corpus for demonstration
_CORPUS: List[Dict[str, Any]] = [
    {"id": "doc-1", "title": "MCP Protocol Overview", "body": "The Model Context Protocol (MCP) enables AI models to interact with tools and resources..."},
    {"id": "doc-2", "title": "Pattern 2: Thin Proxy Architecture", "body": "A thin proxy aggregates multiple MCP servers behind one unified interface..."},
    {"id": "doc-3", "title": "WebSocket Transport in MCP", "body": "WebSocket provides full-duplex communication for real-time MCP sessions..."},
    {"id": "doc-4", "title": "SSE Transport Guide", "body": "Server-Sent Events allow unidirectional streaming from server to client..."},
    {"id": "doc-5", "title": "Circuit Breaker Pattern", "body": "The circuit breaker prevents cascading failures by short-circuiting calls to failing services..."},
    {"id": "doc-6", "title": "Tool Discovery in MCP", "body": "MCP servers expose tools via the tools/list method, returning names, descriptions, and schemas..."},
    {"id": "doc-7", "title": "JSON-RPC 2.0 Specification", "body": "JSON-RPC is a stateless, light-weight remote procedure call protocol encoded in JSON..."},
    {"id": "doc-8", "title": "Async Python Best Practices", "body": "Use asyncio.gather for concurrent tasks, avoid blocking calls in event loops..."},
]


async def search_docs(query: str, top_k: int = 3) -> List[Dict[str, Any]]:
    """
    Full-text keyword search over the corpus.
    Returns top_k most relevant documents.
    """
    # Simulate async I/O (e.g. hitting a vector database)
    await asyncio.sleep(0.05)

    query_lower = query.lower()
    scored = []
    for doc in _CORPUS:
        score = 0
        for word in query_lower.split():
            if word in doc["title"].lower():
                score += 2
            if word in doc["body"].lower():
                score += 1
        if score > 0:
            scored.append((score, doc))

    scored.sort(key=lambda x: x[0], reverse=True)
    results = [
        {"id": doc["id"], "title": doc["title"], "snippet": doc["body"][:100] + "...", "score": score}
        for score, doc in scored[:top_k]
    ]
    return results
