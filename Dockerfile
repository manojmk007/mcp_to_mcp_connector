# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY . .

# ── MCP Proxy Gateway image ───────────────────────────────────────────────────
FROM base AS proxy

EXPOSE 8765 8088

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8088/health || exit 1

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

CMD ["python", "main.py", "--external-mcp1", "--log-level", "INFO"]


# ── MCP1 Local Server image ───────────────────────────────────────────────────
FROM base AS mcp1

EXPOSE 9001

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import asyncio, websockets; asyncio.run(websockets.connect('ws://localhost:9001/ws'))" || exit 1

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

CMD ["python", "-m", "mcp1.server", "--host", "0.0.0.0", "--port", "9001"]
