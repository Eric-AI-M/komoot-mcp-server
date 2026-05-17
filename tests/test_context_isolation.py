"""ContextVar / middleware isolation tests.

These tests prove that two concurrent requests cannot see each other's
AuthManager. If either fails, the server is unsafe for multi-tenant
operation behind the platform gateway.
"""
import asyncio
import json
import os

import pytest

from komoot_mcp.auth import AuthManager
from komoot_mcp.context import (
    clear_request_state,
    get_auth_manager,
    reset_auth_manager,
    set_auth_manager,
)


@pytest.fixture(autouse=True)
def _reset_state():
    """Each test runs in a fresh context."""
    clear_request_state()
    yield
    clear_request_state()


class TestContextVarIsolation:
    def test_set_and_get_returns_same_instance(self):
        am = AuthManager(email="alice@x.com", password="pw1")
        token = set_auth_manager(am)
        try:
            assert get_auth_manager() is am
        finally:
            reset_auth_manager(token)

    def test_reset_restores_previous(self):
        # Lazy-build, then explicitly set, then reset — get_auth_manager
        # should return the lazily-built default (or a fresh one).
        clear_request_state()
        explicit = AuthManager(email="bob@x.com", password="pw2")
        token = set_auth_manager(explicit)
        assert get_auth_manager() is explicit
        reset_auth_manager(token)
        # After reset, the lazy fallback kicks in — different instance.
        fallback = get_auth_manager()
        assert fallback is not explicit

    @pytest.mark.asyncio
    async def test_concurrent_tasks_see_different_managers(self):
        """The smoking gun: two concurrent coroutines must NOT share state."""
        results: dict[str, str | None] = {}
        ready = asyncio.Event()
        proceed = asyncio.Event()

        async def tenant(label: str, email: str):
            am = AuthManager(email=email, password="pw")
            token = set_auth_manager(am)
            try:
                # Yield so both tenants are mid-flight at the same time.
                if label == "alice":
                    ready.set()
                    await proceed.wait()
                else:
                    await ready.wait()
                    proceed.set()
                # If ContextVars work, each tenant still sees its own AM.
                results[label] = get_auth_manager().email
            finally:
                reset_auth_manager(token)

        # asyncio.create_task copies the current context, so each task
        # has its own ContextVar storage. This is the property we rely on.
        await asyncio.gather(
            tenant("alice", "alice@example.com"),
            tenant("bob", "bob@example.com"),
        )

        assert results["alice"] == "alice@example.com"
        assert results["bob"] == "bob@example.com"


class TestUserCredentialsMiddleware:
    """The middleware must extract the JSON header into a ContextVar AM."""

    @pytest.mark.asyncio
    async def test_middleware_installs_credentials_for_request(self):
        from komoot_mcp.middleware import UserCredentialsMiddleware

        captured: dict[str, AuthManager | None] = {"am": None}

        async def downstream(scope, receive, send):
            # Inside the handler, the AM should be set from the header.
            captured["am"] = get_auth_manager()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        mw = UserCredentialsMiddleware(downstream)

        creds = {"email": "tenant@example.com", "password": "secret"}
        scope = {
            "type": "http",
            "path": "/mcp",
            "headers": [
                (b"x-user-credentials", json.dumps(creds).encode("latin-1")),
            ],
        }

        async def receive():
            return {"type": "http.request", "body": b""}

        sent = []
        async def send(msg):
            sent.append(msg)

        await mw(scope, receive, send)

        am = captured["am"]
        assert am is not None
        assert am.email == "tenant@example.com"
        assert am.password == "secret"
        # After the request, state must be cleared.
        clear_request_state()  # idempotent

    @pytest.mark.asyncio
    async def test_middleware_skips_health(self):
        """Health endpoint must not parse creds."""
        from komoot_mcp.middleware import UserCredentialsMiddleware

        async def downstream(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        mw = UserCredentialsMiddleware(downstream)
        scope = {
            "type": "http",
            "path": "/health",
            "headers": [],
        }

        async def receive():
            return {"type": "http.request", "body": b""}
        async def send(msg):
            pass

        # Must not raise even with no header present.
        await mw(scope, receive, send)

    @pytest.mark.asyncio
    async def test_middleware_invalid_json_falls_back_to_env(self):
        """Malformed header should be logged but not crash the request."""
        from komoot_mcp.middleware import UserCredentialsMiddleware

        async def downstream(scope, receive, send):
            # Lazy AM uses env vars.
            am = get_auth_manager()
            assert am is not None
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        mw = UserCredentialsMiddleware(downstream)
        scope = {
            "type": "http",
            "path": "/mcp",
            "headers": [(b"x-user-credentials", b"not-json")],
        }

        async def receive():
            return {"type": "http.request", "body": b""}
        async def send(msg):
            pass

        await mw(scope, receive, send)


class TestInternalSecretMiddleware:
    @pytest.mark.asyncio
    async def test_passes_through_when_secret_unset(self, monkeypatch):
        monkeypatch.delenv("INTERNAL_SECRET", raising=False)
        # Re-import to pick up the env change.
        import importlib
        import komoot_mcp.middleware as mod
        importlib.reload(mod)

        called = []
        async def downstream(scope, receive, send):
            called.append(True)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        mw = mod.InternalSecretMiddleware(downstream)
        scope = {"type": "http", "path": "/mcp", "headers": []}

        async def receive():
            return {"type": "http.request", "body": b""}
        async def send(msg):
            pass

        await mw(scope, receive, send)
        assert called == [True]

    @pytest.mark.asyncio
    async def test_rejects_when_secret_mismatch(self, monkeypatch):
        monkeypatch.setenv("INTERNAL_SECRET", "topsecret")
        import importlib
        import komoot_mcp.middleware as mod
        importlib.reload(mod)

        called = []
        async def downstream(scope, receive, send):
            called.append(True)

        mw = mod.InternalSecretMiddleware(downstream)
        scope = {
            "type": "http",
            "path": "/mcp",
            "headers": [(b"x-internal-secret", b"wrong")],
        }

        captured: list[dict] = []
        async def receive():
            return {"type": "http.request", "body": b""}
        async def send(msg):
            captured.append(msg)

        await mw(scope, receive, send)
        assert called == []  # downstream never invoked
        # First message is response.start with 401.
        assert captured[0]["status"] == 401

    @pytest.mark.asyncio
    async def test_health_bypasses_secret_check(self, monkeypatch):
        monkeypatch.setenv("INTERNAL_SECRET", "topsecret")
        import importlib
        import komoot_mcp.middleware as mod
        importlib.reload(mod)

        called = []
        async def downstream(scope, receive, send):
            called.append(True)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        mw = mod.InternalSecretMiddleware(downstream)
        scope = {"type": "http", "path": "/health", "headers": []}

        async def receive():
            return {"type": "http.request", "body": b""}
        async def send(msg):
            pass

        await mw(scope, receive, send)
        assert called == [True]
