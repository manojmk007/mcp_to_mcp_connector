import asyncio
try:
    import uvloop
except ImportError:
    uvloop = None

from nats.aio.client import Client as NATS

from config import (
    NATS_URL,
    REQUEST_TOPIC,
    RESPONSE_TOPIC
)

from mcp_server import process_message

if uvloop:
    asyncio.set_event_loop_policy(
        uvloop.EventLoopPolicy()
    )

nc = NATS()

async def handle_request(msg):

    try:

        data = msg.data.decode()

        print(f"\n[YOUR MCP] Request Received:")
        print(data)

        # MCP TOOL CALL
        result = await process_message(data)

        response = result["message"]

        # SEND RESPONSE
        await nc.publish(
            RESPONSE_TOPIC,
            response.encode()
        )

        print(f"\n[YOUR MCP] Response Sent:")
        print(response)

    except Exception as e:

        print(f"\n[YOUR MCP] ERROR:")
        print(str(e))

async def main():

    print("[YOUR MCP] Connecting to NATS...")

    await nc.connect(
        servers=[NATS_URL]
    )

    print("[YOUR MCP] Connected")

    # Subscribe
    await nc.subscribe(
        REQUEST_TOPIC,
        cb=handle_request
    )

    print("[YOUR MCP] Waiting For Requests...")

    while True:
        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())