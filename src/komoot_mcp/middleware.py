"""Starlette middleware for the Eric AI platform integration.

Two concerns:

1. **Internal-secret auth.** When ``INTERNAL_SECRET`` is set in the
   environment, every incoming request (except ``/health``) must
   carry a matching ``X-Internal-Secret`` header. This stops random
   internet traffic from reaching the MCP endpoint when it's exposed
   behind the platform gateway. When ``INTERNAL_SECRET`` is unset
   (local dev) the check is skipped.

2. **Per-tenant credentials.** The gateway forwards the calling user's
   Komoot creds as JSON in ``x-user-credentials``:

       {"email": "user@example.com", "password": "..."}

   The middleware parses that header, builds an :class:`AuthManager`,
   pushes it into the request-local ContextVar via
   :func:`komoot_mcp.context.set_auth_manager`, and resets after the
   downstream handler returns. If the header is absent (e.g. stdio
   mode), the env-var fallback in :mod:`komoot_mcp.context` kicks in.
"""
from __future__ import annotations

import json
import logging
import os

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from komoot_mcp.auth import AuthManager
from komoot_mcp.context import (
    clear_request_state,
    reset_auth_manager,
    set_auth_manager,
)


logger = logging.getLogger(__name__)


INTERNAL_SECRET_HEADER = "x-internal-secret"
USER_CREDENTIALS_HEADER = "x-user-credentials"
HEALTH_PATH = "/health"


class InternalSecretMiddleware:
    """Reject non-internal traffic when ``INTERNAL_SECRET`` is set."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        # Cache at construction time — env doesn't change during request lifetime.
        self.secret = os.environ.get("INTERNAL_SECRET")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.secret:
            await self.app(scope, receive, send)
            return

        # Allow health checks without auth so the orchestrator can probe.
        if scope.get("path") == HEALTH_PATH:
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        provided = headers.get(INTERNAL_SECRET_HEADER)
        if provided != self.secret:
            response = JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32000, "message": "Unauthorized — internal requests only"},
                    "id": None,
                },
                status_code=401,
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


class UserCredentialsMiddleware:
    """Extract ``x-user-credentials`` and install it in the ContextVar."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Health endpoint never needs creds — skip the parse work.
        if scope.get("path") == HEALTH_PATH:
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        creds_header = request.headers.get(USER_CREDENTIALS_HEADER)
        token = None
        if creds_header:
            try:
                creds = json.loads(creds_header)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON in %s header", USER_CREDENTIALS_HEADER)
                creds = None
            if isinstance(creds, dict):
                email = creds.get("email")
                password = creds.get("password")
                if email and password:
                    auth = AuthManager(email=email, password=password)
                    token = set_auth_manager(auth)

        try:
            await self.app(scope, receive, send)
        finally:
            # Always reset — never let one tenant's creds outlive the request.
            if token is not None:
                try:
                    reset_auth_manager(token)
                except (ValueError, LookupError):
                    # ContextVar reset can fail across task boundaries; fall
                    # back to a hard clear so we don't leak state.
                    clear_request_state()
            # Belt-and-braces: also drop the lazily-built KomootClient so the
            # next request doesn't see a stale instance bound to old creds.
            clear_request_state()
