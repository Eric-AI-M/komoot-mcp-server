"""Issue #19 — ``komoot_plan_and_upload`` single-tool plan + upload.

The user-facing workflow "plan a route and add it to my Komoot tours"
previously required two MCP tool calls with a ~100k-token GPX flowing
through the LLM in between. The new tool runs both server-side and
returns only the tour ID + URL.

Also covers the lower-level ``upload_gpx_capture_id`` client helper,
which captures the new tour ID from the Komoot response (kompy throws
that away).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import kompy  # the conftest stub
from komoot_mcp.auth import AuthManager
from komoot_mcp.client import KomootAPIError, KomootClient
from komoot_mcp.context import clear_request_state


GPX_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>inline-track</name>
    <trkseg>
      <trkpt lat="49.0" lon="8.4"><ele>120</ele></trkpt>
      <trkpt lat="49.01" lon="8.41"><ele>125</ele></trkpt>
    </trkseg>
  </trk>
</gpx>
"""


class _NoLimit:
    async def acquire(self):
        return None


@pytest.fixture(autouse=True)
def _reset():
    clear_request_state()
    yield
    clear_request_state()


@pytest.fixture
def client():
    am = AuthManager(email="t@x.com", password="pw")
    return KomootClient(am, _NoLimit())


def _register_routing_tools():
    from komoot_mcp.tools import routing_tools

    registered: dict[str, callable] = {}

    class _Mcp:
        def tool(self):
            def deco(fn):
                registered[fn.__name__] = fn
                return fn
            return deco

    routing_tools.register(_Mcp())
    return registered, routing_tools


class _FakeGeocoder:
    def forward(self, q, limit=1):
        return [{
            "lat": 49.0094, "lon": 8.4001,
            "display_name": q, "city": "X",
            "country": "DE", "type": "place",
        }]

    def reverse(self, lat, lon):  # pragma: no cover - unused
        return {}


# ---------------------------------------------------------------------
# The new komoot_plan_and_upload tool
# ---------------------------------------------------------------------


class TestPlanAndUploadTool:
    """The new combo tool must be registered, plan + upload, and not
    leak the 400KB GPX to the response."""

    @pytest.mark.asyncio
    async def test_tool_is_registered(self):
        registered, _ = _register_routing_tools()
        assert "komoot_plan_and_upload" in registered

    @pytest.mark.asyncio
    async def test_happy_path_returns_id_and_url(self, monkeypatch):
        registered, routing_tools = _register_routing_tools()

        fake_gpx = "<gpx><trk><name>plan</name></trk></gpx>"
        fake_plan = {
            "gpx": fake_gpx,
            "distance_km": 12.3,
            "elevation_gain_m": 250.0,
            "duration_minutes": 95.0,
            "waypoints": [(49.0, 8.4), (49.01, 8.41)],
        }

        class _FakeRouting:
            def plan_route(self, **kwargs):
                self.last = kwargs
                return fake_plan

        captured_upload = {}

        class _FakeClient:
            async def upload_gpx_capture_id(self, **kwargs):
                captured_upload.update(kwargs)
                return {"id": 7654321, "status": "uploaded"}

        routing = _FakeRouting()
        monkeypatch.setattr(routing_tools, "get_routing_manager", lambda: routing)
        monkeypatch.setattr(routing_tools, "get_geocoder", lambda: _FakeGeocoder())
        monkeypatch.setattr(routing_tools, "get_client", lambda: _FakeClient())

        out = await registered["komoot_plan_and_upload"](
            start="Freiburg",
            roundtrip=True,
            target_distance_km=70,
            sport="mountain_bike",
            prefer_trails=True,
            avoid_roads=True,
            tour_name="MTB loop",
        )

        # The 400KB GPX must NOT be in the response.
        assert fake_gpx not in out
        assert "<gpx>" not in out
        # Tour ID + URL ARE in the response.
        assert "7654321" in out
        assert "https://www.komoot.com/tour/7654321" in out
        # Plan summary surfaces too.
        assert "12.3 km" in out
        # Sport is mapped from our routing vocab to Komoot's: mountain_bike → mtb.
        assert captured_upload["sport"] == "mtb"
        # Custom tour name flows through.
        assert captured_upload["tour_name"] == "MTB loop"
        # The plan kwargs were threaded through.
        assert routing.last["roundtrip"] is True
        assert routing.last["target_distance_km"] == 70

    @pytest.mark.asyncio
    async def test_upload_failure_does_not_say_successfully(self, monkeypatch):
        registered, routing_tools = _register_routing_tools()

        fake_plan = {
            "gpx": "<gpx><trk><name>plan</name></trk></gpx>",
            "distance_km": 12.3,
            "elevation_gain_m": 250.0,
            "duration_minutes": 95.0,
            "waypoints": [(49.0, 8.4)],
        }

        class _FakeRouting:
            def plan_route(self, **kwargs):
                return fake_plan

        class _FakeClient:
            async def upload_gpx_capture_id(self, **kwargs):
                raise KomootAPIError(
                    "Komoot rejected the upload (HTTP 400). Common 400 "
                    "cause: GPX is in route format (<rte>/<rtept>)..."
                )

        monkeypatch.setattr(routing_tools, "get_routing_manager", lambda: _FakeRouting())
        monkeypatch.setattr(routing_tools, "get_geocoder", lambda: _FakeGeocoder())
        monkeypatch.setattr(routing_tools, "get_client", lambda: _FakeClient())

        out = await registered["komoot_plan_and_upload"](
            start="Freiburg", end="Karlsruhe",
            sport="hike",
        )
        # The literal #17 shape must not appear.
        assert "successfully: False" not in out
        # Must surface a real error.
        assert "upload to Komoot failed" in out
        assert "400" in out
        # But it should still let the user know the plan succeeded.
        assert "12.3 km" in out

    @pytest.mark.asyncio
    async def test_plan_failure_does_not_attempt_upload(self, monkeypatch):
        registered, routing_tools = _register_routing_tools()

        upload_calls = []

        class _FailingRouting:
            def plan_route(self, **kwargs):
                raise RuntimeError("ORS down")

        class _FakeClient:
            async def upload_gpx_capture_id(self, **kwargs):
                upload_calls.append(kwargs)
                return {"id": 1, "status": "uploaded"}

        monkeypatch.setattr(routing_tools, "get_routing_manager", lambda: _FailingRouting())
        monkeypatch.setattr(routing_tools, "get_geocoder", lambda: _FakeGeocoder())
        monkeypatch.setattr(routing_tools, "get_client", lambda: _FakeClient())

        out = await registered["komoot_plan_and_upload"](
            start="Freiburg", end="Karlsruhe", sport="hike",
        )
        assert "Route planning failed" in out
        assert "ORS down" in out
        # The upload path must not have been reached.
        assert upload_calls == []

    @pytest.mark.asyncio
    async def test_no_ors_key_returns_dashboard_hint(self, monkeypatch):
        registered, routing_tools = _register_routing_tools()
        monkeypatch.setattr(routing_tools, "get_routing_manager", lambda: None)

        out = await registered["komoot_plan_and_upload"](
            start="Freiburg", end="Karlsruhe", sport="hike",
        )
        assert "ORS API key" in out

    @pytest.mark.asyncio
    async def test_duplicate_upload_renders_status(self, monkeypatch):
        registered, routing_tools = _register_routing_tools()

        class _FakeRouting:
            def plan_route(self, **kwargs):
                return {
                    "gpx": "<gpx><trk><name>x</name></trk></gpx>",
                    "distance_km": 5.0,
                    "elevation_gain_m": 50.0,
                    "duration_minutes": 30.0,
                    "waypoints": [],
                }

        class _FakeClient:
            async def upload_gpx_capture_id(self, **kwargs):
                return {"id": 99, "status": "duplicate"}

        monkeypatch.setattr(routing_tools, "get_routing_manager", lambda: _FakeRouting())
        monkeypatch.setattr(routing_tools, "get_geocoder", lambda: _FakeGeocoder())
        monkeypatch.setattr(routing_tools, "get_client", lambda: _FakeClient())

        out = await registered["komoot_plan_and_upload"](
            start="Freiburg", end="Karlsruhe", sport="hike",
        )
        assert "duplicate" in out
        assert "99" in out


# ---------------------------------------------------------------------
# upload_gpx_capture_id — the client-side helper used by #19
# ---------------------------------------------------------------------


class _FakeKomootResponse:
    def __init__(self, status_code=201, body=None, text=""):
        self.status_code = status_code
        self._body = body or {}
        self.text = text

    def json(self):
        return self._body


class TestUploadGpxCaptureId:
    """Direct test of the client-side upload helper that pulls the ID
    out of the Komoot response (kompy throws it away)."""

    @pytest.mark.asyncio
    async def test_201_returns_id_and_uploaded_status(self, client, monkeypatch):
        # Stub the kompy connector so _get_api() works.
        api = MagicMock()
        api.authentication = MagicMock()
        api.authentication.get_email_address = MagicMock(return_value="t@x.com")
        api.authentication.get_password = MagicMock(return_value="pw")
        client._api = api

        from komoot_mcp import client as client_mod

        def fake_post(url, auth=None, headers=None, params=None, data=None, **kwargs):
            assert "tours" in url
            assert params["data_type"] == "gpx"
            return _FakeKomootResponse(status_code=201, body={"id": 555})

        monkeypatch.setattr(client_mod.requests, "post", fake_post)

        out = await client.upload_gpx_capture_id(
            gpx_content=GPX_SAMPLE, sport="hike", tour_name="x",
        )
        assert out == {"id": 555, "status": "uploaded"}

    @pytest.mark.asyncio
    async def test_202_marks_duplicate(self, client, monkeypatch):
        api = MagicMock()
        api.authentication = MagicMock()
        api.authentication.get_email_address = MagicMock(return_value="t@x.com")
        api.authentication.get_password = MagicMock(return_value="pw")
        client._api = api

        from komoot_mcp import client as client_mod

        def fake_post(*a, **kw):
            return _FakeKomootResponse(status_code=202, body={"id": 999})

        monkeypatch.setattr(client_mod.requests, "post", fake_post)

        out = await client.upload_gpx_capture_id(
            gpx_content=GPX_SAMPLE, sport="hike",
        )
        assert out == {"id": 999, "status": "duplicate"}

    @pytest.mark.asyncio
    async def test_400_raises_with_status_in_message(self, client, monkeypatch):
        api = MagicMock()
        api.authentication = MagicMock()
        api.authentication.get_email_address = MagicMock(return_value="t@x.com")
        api.authentication.get_password = MagicMock(return_value="pw")
        client._api = api

        from komoot_mcp import client as client_mod

        def fake_post(*a, **kw):
            return _FakeKomootResponse(
                status_code=400, body={}, text="bad gpx",
            )

        monkeypatch.setattr(client_mod.requests, "post", fake_post)

        with pytest.raises(KomootAPIError) as ei:
            await client.upload_gpx_capture_id(
                gpx_content=GPX_SAMPLE, sport="hike",
            )
        msg = str(ei.value)
        assert "400" in msg
        # Helpful diagnosis hint for the most common 400 cause.
        assert "track format" in msg or "trk" in msg
