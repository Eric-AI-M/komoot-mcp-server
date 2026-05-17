"""Per-request execution context for the Komoot MCP server.

Multi-tenancy is achieved with :class:`contextvars.ContextVar` — each
incoming HTTP request runs inside an isolated context so the
``AuthManager`` / ``KomootClient`` it builds never leaks across
tenants. The Starlette middleware in :mod:`komoot_mcp.middleware`
populates the context before the MCP handler runs and resets it
afterwards.

For stdio/local-dev mode (no middleware), the helpers below fall back
to env vars and a process-wide ``RateLimiter`` so behaviour is
unchanged for single-user setups.
"""
from __future__ import annotations

import os
from contextvars import ContextVar
from typing import Optional

from komoot_mcp.auth import AuthManager
from komoot_mcp.client import KomootClient
from komoot_mcp.rate_limiter import RateLimiter
from komoot_mcp.geocoder import Geocoder
from komoot_mcp.routing import RoutingError, RoutingManager


# Per-request state. Tools must NEVER look at module globals — always
# resolve through ``get_*`` helpers so multi-tenant isolation holds.
_auth_var: ContextVar[Optional[AuthManager]] = ContextVar(
    "komoot_auth_manager", default=None
)
_client_var: ContextVar[Optional[KomootClient]] = ContextVar(
    "komoot_client", default=None
)


# Shared singletons that don't carry tenant identity. Rate limiting is
# per-process (we throttle the whole server, not per-user). Geocoder
# and RoutingManager talk to public APIs with no user state.
_rate_limiter: RateLimiter | None = None
_geocoder: Geocoder | None = None
_routing_manager_resolved = False
_routing_manager: RoutingManager | None = None


def get_rate_limiter() -> RateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter


def get_geocoder() -> Geocoder:
    global _geocoder
    if _geocoder is None:
        _geocoder = Geocoder()
    return _geocoder


def get_routing_manager() -> RoutingManager | None:
    global _routing_manager_resolved, _routing_manager
    if not _routing_manager_resolved:
        try:
            _routing_manager = RoutingManager()
        except RoutingError:
            _routing_manager = None
        _routing_manager_resolved = True
    return _routing_manager


def set_auth_manager(auth: AuthManager) -> object:
    """Install an :class:`AuthManager` for the current context.

    Returns a token that can be passed to :func:`reset_auth_manager`
    to undo the change. Middleware MUST reset to avoid leaking creds
    across requests served by the same worker.
    """
    return _auth_var.set(auth)


def reset_auth_manager(token: object) -> None:
    _auth_var.reset(token)  # type: ignore[arg-type]


def get_auth_manager() -> AuthManager:
    """Return the current request's AuthManager.

    Lazily builds one from env vars if none has been installed —
    preserves stdio/local-dev behaviour for single-user setups.
    """
    am = _auth_var.get()
    if am is None:
        am = AuthManager()
        _auth_var.set(am)
        # The lazy fallback is not reset — stdio mode is single-tenant
        # by design, so we keep the auth for the life of the process.
    return am


def get_client() -> KomootClient:
    """Return the current request's KomootClient, building lazily."""
    c = _client_var.get()
    if c is None:
        c = KomootClient(get_auth_manager(), get_rate_limiter())
        _client_var.set(c)
    return c


def clear_request_state() -> None:
    """Reset both auth and client ContextVars to their defaults.

    Middleware calls this in a ``finally`` block to make absolutely
    sure no tenant state is held by the worker after a response.
    """
    _auth_var.set(None)
    _client_var.set(None)
