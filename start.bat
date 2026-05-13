@echo off
REM ── MCP Proxy Gateway — Windows Start Script ───────────────────────────────
setlocal

echo.
echo ╔══════════════════════════════════════════════════════════╗
echo ║         MCP Proxy Gateway — Starting...                 ║
echo ╚══════════════════════════════════════════════════════════╝
echo.

REM 1. Install dependencies
pip install -r requirements.txt
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to install dependencies.
    exit /b 1
)

REM 2. Copy .env if not present
if not exist .env (
    echo [INFO] Creating .env from .env.example...
    copy .env.example .env
)

REM 3. Start MCP1 in a new window
echo [INFO] Starting MCP1 local server on port 9001...
start "MCP1 Server" cmd /k "python -m mcp1.server --port 9001"

REM Give MCP1 time to start
timeout /t 2 /nobreak >nul

REM 4. Start the proxy
echo [INFO] Starting MCP Proxy Gateway...
echo [INFO]   WebSocket : ws://localhost:8765/ws
echo [INFO]   HTTP      : http://localhost:8088
echo [INFO]   Health    : http://localhost:8088/health
echo.

python main.py --external-mcp1 --log-level INFO %*

endlocal
