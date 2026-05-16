"""Komoot MCP Server — browse, download, upload, and plan routes."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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

# Create the MCP server
mcp = FastMCP("Komoot MCP Server")

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


def main():
    """Entry point for the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
