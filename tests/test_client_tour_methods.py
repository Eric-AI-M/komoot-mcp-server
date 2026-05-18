"""Regression tests for tour-method calls that mismatched kompy signatures.

Two production bugs are pinned down here:

* ``komoot_get_tour_gpx`` raised
  ``Tour.generate_gpx_track() missing 1 required positional argument:
  'authentication'``. The fix threads ``api.authentication`` through.
* ``komoot_get_tour_timeline`` raised
  ``Tour._create_tour_summary() missing 1 required positional argument:
  'tour_summary'``. The fix reads the eagerly-populated ``tour.summary``
  attribute instead of calling the static method as if it were bound.

We also assert that ``get_tour_coordinates`` now passes ``authentication``
to ``Tour.generate_coordinates``, because it carries the same kompy
signature requirement.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import kompy  # the conftest stub
import pytest

from komoot_mcp.auth import AuthManager
from komoot_mcp.client import KomootClient


class _NoLimit:
    async def acquire(self):
        return None


@pytest.fixture
def client():
    am = AuthManager(email="t@x.com", password="pw")
    return KomootClient(am, _NoLimit())


def _make_tour_stub(*, with_summary=True, with_info=True):
    """A bare ``kompy.Tour`` instance.

    We bypass ``Tour.__init__`` (the real one needs a fully-shaped API
    dict) and just construct a blank object that ``isinstance(tour,
    kompy.Tour)`` still accepts — that's the branch we want exercised
    in the production code. We then attach the attributes the client
    reads. Works against both the lightweight conftest stub and the real
    kompy install because the only thing we need is the class identity.
    """
    tour = kompy.Tour.__new__(kompy.Tour)
    tour.id = "42"
    tour.name = "stub-tour"
    tour.coordinates = []
    tour.gpx_track = None
    tour.segments = []
    tour.path = []
    if with_summary:
        # Mimic the TourSummary shape: surfaces + way_types lists of
        # objects with the production attribute names.
        surface = MagicMock(surface_type="asphalt", amount=0.7)
        way = MagicMock(way_type="road", amount=0.7)
        tour.summary = MagicMock(surfaces=[surface], way_types=[way])
    else:
        tour.summary = None
    if with_info:
        ti = MagicMock(
            tour_information_type="warning",
            segments=[MagicMock(start_index_point=0, end_index_point=5)],
        )
        tour.tour_information = [ti]
    else:
        tour.tour_information = None
    return tour


def _install_api_stub(client, tour, generate_gpx=None, generate_coords=None):
    """Wire ``client._api`` to a minimal connector returning ``tour``."""
    auth = MagicMock()
    api = MagicMock()
    api.authentication = auth
    api.get_tour_by_id = MagicMock(return_value=tour)
    client._api = api
    # Bind the test-supplied implementations as bound methods so the
    # ``tour.generate_*`` calls see ``self`` properly.
    if generate_gpx is not None:
        tour.generate_gpx_track = generate_gpx
    if generate_coords is not None:
        tour.generate_coordinates = generate_coords
    return api, auth


class TestGetTourTimeline:
    @pytest.mark.asyncio
    async def test_returns_events_from_populated_summary(self, client):
        tour = _make_tour_stub(with_summary=True)
        _install_api_stub(client, tour)
        result = await client.get_tour_timeline(42)
        # Should not raise the original
        # ``missing 1 required positional argument: 'tour_summary'`` —
        # and should surface both surfaces and way_types.
        assert isinstance(result, list)
        types = {e["type"] for e in result}
        assert "surface" in types
        assert "way_type" in types

    @pytest.mark.asyncio
    async def test_returns_empty_when_summary_missing(self, client):
        tour = _make_tour_stub(with_summary=False)
        _install_api_stub(client, tour)
        result = await client.get_tour_timeline(42)
        assert result == []


class TestGetTourGpx:
    @pytest.mark.asyncio
    async def test_passes_authentication_to_kompy(self, client):
        tour = _make_tour_stub()

        gpx_obj = MagicMock()
        gpx_obj.to_xml.return_value = "<gpx><trk/></gpx>"

        captured = {}

        def fake_generate(authentication):
            # The fix: kompy requires the auth object as a positional arg.
            captured["auth"] = authentication
            tour.gpx_track = gpx_obj
            return True

        _, auth = _install_api_stub(client, tour, generate_gpx=fake_generate)
        out = await client.get_tour_gpx(42)
        # Auth must be threaded through — this is the exact arg the
        # original ``missing 1 required positional argument`` was about.
        assert captured["auth"] is auth
        assert out == "<gpx><trk/></gpx>"


class TestGetTourCoordinates:
    @pytest.mark.asyncio
    async def test_passes_authentication_to_kompy(self, client):
        tour = _make_tour_stub()

        captured = {}

        def fake_generate(authentication):
            captured["auth"] = authentication
            tour.coordinates = [{"lat": 1, "lng": 2}]
            return True

        _, auth = _install_api_stub(
            client, tour, generate_coords=fake_generate,
        )
        out = await client.get_tour_coordinates(42)
        assert captured["auth"] is auth
        assert out == [{"lat": 1, "lng": 2}]


class TestGetTourSurfaces:
    @pytest.mark.asyncio
    async def test_returns_serialized_tour_information(self, client):
        tour = _make_tour_stub(with_info=True)
        _install_api_stub(client, tour)
        result = await client.get_tour_surfaces(42)
        assert isinstance(result, list)
        assert result[0]["type"] == "warning"
        assert result[0]["segments"][0]["from"] == 0
        assert result[0]["segments"][0]["to"] == 5
