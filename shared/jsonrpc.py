"""
shared/jsonrpc.py - JSON-RPC 2.0 helpers, standard error codes, and message builders.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional, Union

from shared.models import JSONRPCError, JSONRPCRequest, JSONRPCResponse, JSONRPCNotification


# ---------------------------------------------------------------------------
# Standard JSON-RPC 2.0 error codes
# ---------------------------------------------------------------------------

class ErrorCode:
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603

    # MCP-specific application codes (>= -32000)
    TOOL_NOT_FOUND = -32000
    DOWNSTREAM_UNAVAILABLE = -32001
    TIMEOUT = -32002
    CIRCUIT_OPEN = -32003
    SESSION_NOT_FOUND = -32004
    UPSTREAM_ERROR = -32005


# ---------------------------------------------------------------------------
# Builder helpers
# ---------------------------------------------------------------------------

def make_request(
    method: str,
    params: Optional[Dict[str, Any]] = None,
    req_id: Optional[Union[str, int]] = None,
) -> JSONRPCRequest:
    req = JSONRPCRequest(method=method, params=params)
    if req_id is not None:
        req.id = req_id
    return req


def make_success(
    result: Any,
    req_id: Optional[Union[str, int]] = None,
) -> JSONRPCResponse:
    return JSONRPCResponse(result=result, id=req_id)


def make_error(
    code: int,
    message: str,
    data: Optional[Any] = None,
    req_id: Optional[Union[str, int]] = None,
) -> JSONRPCResponse:
    return JSONRPCResponse(
        error=JSONRPCError(code=code, message=message, data=data),
        id=req_id,
    )


def make_notification(method: str, params: Optional[Dict[str, Any]] = None) -> JSONRPCNotification:
    return JSONRPCNotification(method=method, params=params)


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def encode(obj: Union[JSONRPCRequest, JSONRPCResponse, JSONRPCNotification]) -> str:
    return obj.model_dump_json(exclude_none=False)


def decode_request(raw: Union[str, bytes]) -> JSONRPCRequest:
    data = json.loads(raw)
    return JSONRPCRequest(**data)


def decode_response(raw: Union[str, bytes]) -> JSONRPCResponse:
    data = json.loads(raw)
    return JSONRPCResponse(**data)


def is_notification(data: Dict[str, Any]) -> bool:
    """A JSON-RPC notification has no 'id' field."""
    return "id" not in data or data.get("id") is None


def validate_jsonrpc(data: Dict[str, Any]) -> Optional[str]:
    """
    Basic JSON-RPC 2.0 structural validation.
    Returns None if valid, or an error string if invalid.
    """
    if not isinstance(data, dict):
        return "Message must be a JSON object"
    if data.get("jsonrpc") != "2.0":
        return "jsonrpc field must be '2.0'"
    if "method" not in data:
        return "Missing required field: method"
    if not isinstance(data["method"], str):
        return "method must be a string"
    return None


# ---------------------------------------------------------------------------
# MCP method constants
# ---------------------------------------------------------------------------

class MCPMethod:
    # Lifecycle
    INITIALIZE = "initialize"
    INITIALIZED = "notifications/initialized"
    PING = "ping"
    # Tools
    TOOLS_LIST = "tools/list"
    TOOLS_CALL = "tools/call"
    # Resources
    RESOURCES_LIST = "resources/list"
    RESOURCES_READ = "resources/read"
    # Prompts
    PROMPTS_LIST = "prompts/list"
    PROMPTS_GET = "prompts/get"
    # Notifications
    CANCELLED = "notifications/cancelled"
    PROGRESS = "notifications/progress"


# ---------------------------------------------------------------------------
# MCP initialize request/response helpers
# ---------------------------------------------------------------------------

def build_initialize_request(client_name: str = "mcp-proxy", version: str = "1.0.0") -> JSONRPCRequest:
    return make_request(
        MCPMethod.INITIALIZE,
        params={
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {},
                "roots": {"listChanged": False},
            },
            "clientInfo": {"name": client_name, "version": version},
        },
    )


def build_tools_list_request() -> JSONRPCRequest:
    return make_request(MCPMethod.TOOLS_LIST, params={})


def build_tools_call_request(
    tool_name: str,
    arguments: Dict[str, Any],
    req_id: Optional[Union[str, int]] = None,
) -> JSONRPCRequest:
    return make_request(
        MCPMethod.TOOLS_CALL,
        params={"name": tool_name, "arguments": arguments},
        req_id=req_id,
    )
