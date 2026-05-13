"""
tests/test_jsonrpc.py - Unit tests for JSON-RPC helpers.
"""
from __future__ import annotations

import pytest

from shared.jsonrpc import (
    ErrorCode,
    make_error,
    make_request,
    make_success,
    validate_jsonrpc,
    decode_request,
    decode_response,
    build_initialize_request,
    build_tools_list_request,
    build_tools_call_request,
    MCPMethod,
)


def test_make_request_basic():
    req = make_request("tools/list")
    assert req.jsonrpc == "2.0"
    assert req.method == "tools/list"
    assert req.id is not None


def test_make_success():
    resp = make_success({"tools": []}, req_id="abc")
    assert resp.result == {"tools": []}
    assert resp.error is None
    assert resp.id == "abc"


def test_make_error():
    resp = make_error(ErrorCode.TOOL_NOT_FOUND, "Tool not found", req_id="xyz")
    assert resp.error is not None
    assert resp.error.code == ErrorCode.TOOL_NOT_FOUND
    assert resp.result is None


def test_validate_jsonrpc_valid():
    data = {"jsonrpc": "2.0", "method": "ping", "id": 1}
    assert validate_jsonrpc(data) is None


def test_validate_jsonrpc_missing_method():
    data = {"jsonrpc": "2.0", "id": 1}
    err = validate_jsonrpc(data)
    assert err is not None
    assert "method" in err


def test_validate_jsonrpc_wrong_version():
    data = {"jsonrpc": "1.0", "method": "ping"}
    err = validate_jsonrpc(data)
    assert err is not None


def test_build_initialize_request():
    req = build_initialize_request("test-client", "0.1")
    assert req.method == MCPMethod.INITIALIZE
    assert req.params["clientInfo"]["name"] == "test-client"
    assert "protocolVersion" in req.params


def test_build_tools_call_request():
    req = build_tools_call_request("ping", {"key": "value"})
    assert req.method == MCPMethod.TOOLS_CALL
    assert req.params["name"] == "ping"
    assert req.params["arguments"] == {"key": "value"}


def test_decode_request():
    raw = '{"jsonrpc": "2.0", "method": "ping", "id": "1"}'
    req = decode_request(raw)
    assert req.method == "ping"
    assert req.id == "1"


def test_decode_response():
    raw = '{"jsonrpc": "2.0", "result": {"pong": true}, "id": "1"}'
    resp = decode_response(raw)
    assert resp.result == {"pong": True}
