"""
config.py - Central configuration loader.
Supports env vars and .env file overrides.
"""
from __future__ import annotations

import os
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class ProxyConfig(BaseSettings):
    # Server binding
    HOST: str = Field("0.0.0.0", env="PROXY_HOST")
    WS_PORT: int = Field(8765, env="PROXY_WS_PORT")
    HTTP_PORT: int = Field(8088, env="PROXY_HTTP_PORT")

    # Logging
    LOG_LEVEL: str = Field("INFO", env="LOG_LEVEL")
    JSON_LOGS: bool = Field(False, env="JSON_LOGS")

    # MCP1 local server
    MCP1_ENABLED: bool = Field(True, env="MCP1_ENABLED")
    MCP1_HOST: str = Field("127.0.0.1", env="MCP1_HOST")
    MCP1_PORT: int = Field(9001, env="MCP1_PORT")

    # Thunai MCP
    THUNAI_MCP_URL: str = Field(
        "https://api.thunai.ai/mcp-service/thunai/service/mcp-sse/mcp",
        env="THUNAI_MCP_URL",
    )
    THUNAI_ENABLED: bool = Field(True, env="THUNAI_ENABLED")

    # Circuit breaker
    CB_FAILURE_THRESHOLD: int = Field(5, env="CB_FAILURE_THRESHOLD")
    CB_RECOVERY_TIMEOUT_S: float = Field(30.0, env="CB_RECOVERY_TIMEOUT_S")

    # Timeouts
    REQUEST_TIMEOUT_S: float = Field(30.0, env="REQUEST_TIMEOUT_S")
    HEARTBEAT_INTERVAL_S: float = Field(15.0, env="HEARTBEAT_INTERVAL_S")
    RECONNECT_INTERVAL_S: float = Field(5.0, env="RECONNECT_INTERVAL_S")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


# Singleton
_config: Optional[ProxyConfig] = None


def get_config() -> ProxyConfig:
    global _config
    if _config is None:
        _config = ProxyConfig()
    return _config
