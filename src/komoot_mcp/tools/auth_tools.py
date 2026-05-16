"""Auth tools for Komoot MCP server."""

auth_manager = None  # Set by server.py


def register(mcp):
    @mcp.tool()
    async def komoot_login() -> str:
        """Log in to Komoot with credentials from environment (KOMOOT_EMAIL, KOMOOT_PASSWORD).
        Call this first before using any other Komoot tools."""
        if auth_manager.is_authenticated():
            return f"Already authenticated as user {auth_manager.get_user_id()}"
        try:
            auth_manager.login()
            return f"Successfully authenticated as user {auth_manager.get_user_id()}"
        except Exception as e:
            return f"Login failed: {e}"
