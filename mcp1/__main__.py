"""
mcp1/__main__.py - Allows running MCP1 as a module: python -m mcp1.server
"""
from mcp1.server import main
import asyncio

if __name__ == "__main__":
    asyncio.run(main())
