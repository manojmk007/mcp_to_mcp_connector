#!/usr/bin/env bash
# ── MCP Proxy Gateway — Linux/Mac Start Script ─────────────────────────────
set -e

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║         MCP Proxy Gateway — Starting...                 ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# 1. Install dependencies
pip install -r requirements.txt

# 2. Copy .env if not present
if [ ! -f .env ]; then
    echo "[INFO] Creating .env from .env.example..."
    cp .env.example .env
fi

# 3. Start MCP1 in the background
echo "[INFO] Starting MCP1 local server on port 9001..."
python -m mcp1.server --port 9001 &
MCP1_PID=$!
echo "[INFO] MCP1 PID: $MCP1_PID"

# Give MCP1 time to start
sleep 2

# 4. Trap to kill MCP1 on exit
trap "echo '[INFO] Stopping MCP1...'; kill $MCP1_PID 2>/dev/null || true" EXIT

# 5. Start the proxy
echo "[INFO] Starting MCP Proxy Gateway..."
echo "[INFO]   WebSocket : ws://localhost:8765/ws"
echo "[INFO]   HTTP      : http://localhost:8088"
echo "[INFO]   Health    : http://localhost:8088/health"
echo ""

python main.py --external-mcp1 --log-level INFO "$@"
