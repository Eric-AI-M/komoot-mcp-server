"""Auth tools for Komoot MCP server."""

from komoot_mcp.context import get_auth_manager


def register(mcp):
    @mcp.tool()
    async def komoot_login() -> str:
        """Log in to Komoot.

        In platform mode the gateway injects per-user credentials via
        the ``x-user-credentials`` header — no environment variables
        needed. In stdio/local mode, ``KOMOOT_EMAIL`` and
        ``KOMOOT_PASSWORD`` env vars are used instead.
        """
        auth_manager = get_auth_manager()
        if auth_manager.is_authenticated():
            return f"Already authenticated as user {auth_manager.get_user_id()}"
        try:
            auth_manager.login()
            return f"Successfully authenticated as user {auth_manager.get_user_id()}"
        except Exception as e:
            return f"Login failed: {e}"
