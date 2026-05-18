"""Test configuration and fixtures.

Installs a lightweight kompy stub before any project module is imported,
so test environments without the real kompy package can still load
``komoot_mcp.client`` and friends.
"""
import os
import sys
from types import ModuleType

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _install_kompy_stub_if_missing() -> None:
    try:
        import kompy  # noqa: F401
        return
    except ModuleNotFoundError:
        pass

    class _StubTour:
        def __init__(self, tour_id=1, name="stub"):
            self.id = tour_id
            self.name = name
            self.sport = "hike"
            self.status = "private"
            self.distance = 5000
            self.elevation_up = 200
            self.elevation_down = 200
            self.duration = 3600

    class _StubAuthentication:
        def __init__(self, email, password):
            self._email = email
            self._password = password

        def get_username(self):
            return f"user-{self._email}"

        def get_email_address(self):
            return self._email

    class _StubConnector:
        def __init__(self, email, password):
            self.email = email
            self.password = password

        def get_tours(self, **kwargs):
            return [_StubTour(1, f"tour-for-{self.email}")]

    stub = ModuleType("kompy")
    stub.KomootConnector = _StubConnector
    stub.Authentication = _StubAuthentication
    stub.Tour = _StubTour
    sys.modules["kompy"] = stub


def _install_openrouteservice_stub_if_missing() -> None:
    """Install a minimal ``openrouteservice`` stub so RoutingManager can
    be instantiated in tests without the real ORS dependency.

    The stub records the key it was constructed with so tests can assert
    that the per-request key was threaded through correctly.
    """
    try:
        import openrouteservice  # noqa: F401
        return
    except ModuleNotFoundError:
        pass

    class _StubApiError(Exception):
        pass

    class _StubClient:
        def __init__(self, key=None):
            self.key = key

        def directions(self, **kwargs):  # pragma: no cover - not exercised in unit tests
            raise NotImplementedError("RoutingManager tests should mock plan_route, not call ORS")

    stub = ModuleType("openrouteservice")
    stub.Client = _StubClient

    exceptions = ModuleType("openrouteservice.exceptions")
    exceptions.ApiError = _StubApiError
    stub.exceptions = exceptions

    sys.modules["openrouteservice"] = stub
    sys.modules["openrouteservice.exceptions"] = exceptions


_install_kompy_stub_if_missing()
_install_openrouteservice_stub_if_missing()
