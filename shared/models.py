"""
shared/models.py - Core Pydantic models for the MCP Proxy Gateway system.
Defines all data structures for JSON-RPC, tool registry, sessions, and metrics.
"""
from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class TransportType(str, Enum):
    WEBSOCKET = "websocket"
    SSE = "sse"
    STDIO = "stdio"


class ConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"


class CircuitState(str, Enum):
    CLOSED = "closed"       # Normal operation
    OPEN = "open"           # Failing – reject fast
    HALF_OPEN = "half_open" # Probe request allowed


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 primitives
# ---------------------------------------------------------------------------

class JSONRPCRequest(BaseModel):
    jsonrpc: str = "2.0"
    method: str
    params: Optional[Dict[str, Any]] = None
    id: Optional[Union[str, int]] = Field(default_factory=lambda: str(uuid.uuid4()))

    model_config = {"extra": "allow"}


class JSONRPCError(BaseModel):
    code: int
    message: str
    data: Optional[Any] = None


class JSONRPCResponse(BaseModel):
    jsonrpc: str = "2.0"
    result: Optional[Any] = None
    error: Optional[JSONRPCError] = None
    id: Optional[Union[str, int]] = None

    model_config = {"extra": "allow"}


class JSONRPCNotification(BaseModel):
    """One-way notification (no id field)."""
    jsonrpc: str = "2.0"
    method: str
    params: Optional[Dict[str, Any]] = None

    model_config = {"extra": "allow"}


# ---------------------------------------------------------------------------
# MCP protocol specific models
# ---------------------------------------------------------------------------

class MCPToolParameter(BaseModel):
    """Schema for a single tool parameter."""
    type: str
    description: Optional[str] = None
    enum: Optional[List[Any]] = None
    default: Optional[Any] = None
    items: Optional[Dict[str, Any]] = None  # For array types

    model_config = {"extra": "allow"}


class MCPToolSchema(BaseModel):
    """JSON Schema for a tool's input."""
    type: str = "object"
    properties: Dict[str, Any] = Field(default_factory=dict)
    required: List[str] = Field(default_factory=list)

    model_config = {"extra": "allow"}


class MCPTool(BaseModel):
    """A single MCP tool definition as returned by tools/list."""
    name: str
    description: Optional[str] = None
    inputSchema: Optional[Dict[str, Any]] = None  # Raw schema dict from MCP server

    model_config = {"extra": "allow"}


class MCPToolCallResult(BaseModel):
    """Result of a tools/call invocation."""
    content: List[Dict[str, Any]] = Field(default_factory=list)
    isError: bool = False

    model_config = {"extra": "allow"}


class MCPCapabilities(BaseModel):
    tools: Optional[Dict[str, Any]] = None
    resources: Optional[Dict[str, Any]] = None
    prompts: Optional[Dict[str, Any]] = None
    logging: Optional[Dict[str, Any]] = None

    model_config = {"extra": "allow"}


class MCPServerInfo(BaseModel):
    name: str
    version: str

    model_config = {"extra": "allow"}


class MCPInitializeResult(BaseModel):
    protocolVersion: str
    capabilities: MCPCapabilities
    serverInfo: MCPServerInfo

    model_config = {"extra": "allow"}


# ---------------------------------------------------------------------------
# Tool Registry models
# ---------------------------------------------------------------------------

class ToolRegistryEntry(BaseModel):
    """Entry in the unified tool registry."""
    tool: MCPTool
    downstream_id: str          # Which downstream MCP owns this tool
    transport_type: TransportType
    registered_at: float = Field(default_factory=time.time)
    call_count: int = 0
    error_count: int = 0
    total_latency_ms: float = 0.0

    @property
    def avg_latency_ms(self) -> float:
        if self.call_count == 0:
            return 0.0
        return self.total_latency_ms / self.call_count


class ToolRegistry(BaseModel):
    """In-memory unified tool registry."""
    entries: Dict[str, ToolRegistryEntry] = Field(default_factory=dict)
    last_updated: float = Field(default_factory=time.time)

    def register(self, tool: MCPTool, downstream_id: str, transport: TransportType) -> None:
        self.entries[tool.name] = ToolRegistryEntry(
            tool=tool,
            downstream_id=downstream_id,
            transport_type=transport,
        )
        self.last_updated = time.time()

    def lookup(self, tool_name: str) -> Optional[ToolRegistryEntry]:
        return self.entries.get(tool_name)

    def tools_for_downstream(self, downstream_id: str) -> List[MCPTool]:
        return [e.tool for e in self.entries.values() if e.downstream_id == downstream_id]

    def all_tools(self) -> List[MCPTool]:
        return [e.tool for e in self.entries.values()]

    def record_call(self, tool_name: str, latency_ms: float, error: bool = False) -> None:
        entry = self.entries.get(tool_name)
        if entry:
            entry.call_count += 1
            entry.total_latency_ms += latency_ms
            if error:
                entry.error_count += 1


# ---------------------------------------------------------------------------
# Downstream MCP server config
# ---------------------------------------------------------------------------

class DownstreamConfig(BaseModel):
    """Configuration for a downstream MCP server connection."""
    id: str
    name: str
    transport: TransportType
    url: str
    reconnect_interval_s: float = 5.0
    max_reconnect_attempts: int = 10
    request_timeout_s: float = 30.0
    heartbeat_interval_s: float = 15.0
    # Circuit-breaker thresholds
    cb_failure_threshold: int = 5
    cb_recovery_timeout_s: float = 30.0
    headers: Dict[str, str] = Field(default_factory=dict)
    enabled: bool = True


# ---------------------------------------------------------------------------
# Session models
# ---------------------------------------------------------------------------

class ClientSession(BaseModel):
    """Represents an upstream client session connected to the proxy."""
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    connected_at: float = Field(default_factory=time.time)
    last_seen: float = Field(default_factory=time.time)
    remote_addr: Optional[str] = None
    protocol_version: Optional[str] = None
    client_info: Optional[Dict[str, Any]] = None
    request_count: int = 0

    def touch(self) -> None:
        self.last_seen = time.time()


# ---------------------------------------------------------------------------
# Metrics models
# ---------------------------------------------------------------------------

class ToolCallMetric(BaseModel):
    """Single tool-call execution record for observability."""
    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: Optional[str] = None
    tool_name: str
    downstream_id: str
    started_at: float = Field(default_factory=time.time)
    finished_at: Optional[float] = None
    latency_ms: Optional[float] = None
    success: bool = True
    error_message: Optional[str] = None

    def finish(self, success: bool = True, error: Optional[str] = None) -> None:
        self.finished_at = time.time()
        self.latency_ms = (self.finished_at - self.started_at) * 1000
        self.success = success
        self.error_message = error


class ProxyMetrics(BaseModel):
    """Aggregate proxy-level metrics."""
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    total_latency_ms: float = 0.0
    downstream_metrics: Dict[str, Dict[str, Any]] = Field(default_factory=dict)

    @property
    def avg_latency_ms(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.total_latency_ms / self.total_requests

    @property
    def error_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.failed_requests / self.total_requests


# ---------------------------------------------------------------------------
# Health-check models
# ---------------------------------------------------------------------------

class DownstreamHealth(BaseModel):
    downstream_id: str
    name: str
    state: ConnectionState
    circuit_state: CircuitState
    tool_count: int
    last_connected: Optional[float] = None
    last_error: Optional[str] = None


class HealthReport(BaseModel):
    status: str  # "healthy" | "degraded" | "unhealthy"
    uptime_s: float
    tool_count: int
    downstream_health: List[DownstreamHealth]
    metrics: ProxyMetrics
    timestamp: float = Field(default_factory=time.time)
