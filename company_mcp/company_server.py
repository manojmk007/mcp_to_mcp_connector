import asyncio
try:
    import uvloop
except ImportError:
    uvloop = None

from mcp.server.fastmcp import FastMCP
from nats.aio.client import Client as NATS

from config import (
    NATS_URL,
    REQUEST_TOPIC,
    RESPONSE_TOPIC
)

if uvloop:
    asyncio.set_event_loop_policy(
        uvloop.EventLoopPolicy()
    )

mcp = FastMCP("Company-MCP")

nc = NATS()


async def response_handler(msg):

    data = msg.data.decode()

    print(f"\n[COMPANY MCP] Response Received:")
    print(data)


@mcp.tool()
async def send_message(message: str):

    await nc.publish(
        REQUEST_TOPIC,
        message.encode()
    )

    return {
        "status": "sent",
        "message": message
    }



async def startup():

    print("[COMPANY MCP] Connecting to NATS...")

    await nc.connect(
        servers=[NATS_URL]
    )

    print("[COMPANY MCP] Connected")

    await nc.subscribe(
        RESPONSE_TOPIC,
        cb=response_handler
    )

    print("[COMPANY MCP] Listening For Responses...")


async def main():

    await startup()

    # DEMO REQUEST
    await send_message(
        "Hello From Company MCP"
    )

    while True:
        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())