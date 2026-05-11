import os
NATS_URL = os.getenv("NATS_URL", "nats://nats:4222")

REQUEST_TOPIC = "mcp.request"
RESPONSE_TOPIC = "mcp.response"