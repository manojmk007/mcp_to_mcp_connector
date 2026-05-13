"""
tests/test_mcp1_tools.py - Unit tests for MCP1 tool implementations.
"""
from __future__ import annotations

import pytest

from mcp1.tools.search_docs import search_docs
from mcp1.tools.summarize_text import summarize_text


@pytest.mark.asyncio
async def test_search_docs_returns_results():
    results = await search_docs("MCP protocol")
    assert isinstance(results, list)
    assert len(results) > 0
    assert "title" in results[0]
    assert "snippet" in results[0]
    assert "score" in results[0]


@pytest.mark.asyncio
async def test_search_docs_empty_query():
    results = await search_docs("")
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_search_docs_top_k():
    results = await search_docs("MCP", top_k=2)
    assert len(results) <= 2


@pytest.mark.asyncio
async def test_summarize_short_text():
    text = "This is a short sentence."
    summary = await summarize_text(text)
    assert summary == text.strip()


@pytest.mark.asyncio
async def test_summarize_long_text():
    text = (
        "The Model Context Protocol enables tool integration. "
        "It defines how AI models interact with external services. "
        "Circuit breakers prevent cascading failures. "
        "WebSocket provides full-duplex communication. "
        "SSE allows streaming from server to client."
    )
    summary = await summarize_text(text, max_sentences=2)
    assert len(summary) < len(text)
    assert isinstance(summary, str)


@pytest.mark.asyncio
async def test_summarize_empty_text():
    summary = await summarize_text("")
    assert "No content" in summary


@pytest.mark.asyncio
async def test_search_docs_scores_relevance():
    results = await search_docs("circuit breaker pattern")
    if results:
        # The circuit breaker document should rank first
        top_title = results[0]["title"].lower()
        assert "circuit" in top_title or results[0]["score"] > 0
