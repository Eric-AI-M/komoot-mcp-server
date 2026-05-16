"""Komoot MCP Server — browse, download, upload, and plan routes."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from starlette.requests import Request
from starlette.responses import JSONResponse
from mcp.server import FastMCP

from komoot_mcp.auth import AuthManager
from komoot_mcp.rate_limiter import RateLimiter
from komoot_mcp.client import KomootClient
from komoot_mcp.geocoder import Geocoder
from komoot_mcp.routing import RoutingManager, RoutingError

from komoot_mcp.tools import (
    auth_tools,
    browse_tools,
    data_tools,
    write_tools,
    routing_tools,
)


def create_server(host="127.0.0.1", port=8000):
    """Create and configure the MCP server with all tools registered."""
    mcp = FastMCP(
        "Komoot MCP Server",
        host=host,
        port=port,
        streamable_http_path="/mcp",
    )

    # Add health check endpoint
    @mcp.custom_route("/health", methods=["GET"])
    async def health_check(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    # Initialize components
    auth_manager = AuthManager()
    rate_limiter = RateLimiter()
    komoot_client = KomootClient(auth_manager, rate_limiter)
    geocoder = Geocoder()

    try:
        routing_manager = RoutingManager()
    except RoutingError:
        routing_manager = None

    # Wire dependencies into tool modules
    auth_tools.auth_manager = auth_manager
    browse_tools.client = komoot_client
    data_tools.client = komoot_client
    write_tools.client = komoot_client
    routing_tools.geocoder = geocoder
    routing_tools.routing = routing_manager

    # Register all tools
    auth_tools.register(mcp)
    browse_tools.register(mcp)
    data_tools.register(mcp)
    write_tools.register(mcp)
    routing_tools.register(mcp)

    return mcp


def main():
    """Entry point for the MCP server."""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", default="stdio", choices=["stdio", "http"])
    parser.add_argument("--port", type=int, default=int(os.environ.get("MCP_PORT", "3007")))
    parser.add_argument("--host", default=os.environ.get("MCP_HOST", "0.0.0.0"))
    args = parser.parse_args()

    mcp = create_server(host=args.host, port=args.port)

    if args.transport == "http":
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
