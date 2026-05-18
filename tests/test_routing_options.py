"""Routing layer regression tests covering profile-specific avoid_features
and waypoint snapping (lon/lat order + extended radiuses).

These pin down the fixes for two production bugs reported by user testing:

* ORS rejects ``avoid_features=["highways"]`` for any cycling-* or foot-*
  profile (error 2003). The fix lives in ``routing._build_options``.
* ORS defaults to a 350m snap radius and expects ``[longitude, latitude]``
  coordinates. Geocoded points fed in as ``(lat, lon)`` failed with
  error 2010 even at well-known places. The fix lives in
  ``routing._to_lon_lat`` + the new ``radiuses`` kwarg.
"""
from __future__ import annotations

from typing import Any

import pytest

from komoot_mcp.routing import (
    _DEFAULT_SNAP_RADIUS_M,
    _filter_avoid_features,
    _to_lon_lat,
    RoutingManager,
)


class _FakeOrsClient:
    """Stand-in for ``openrouteservice.Client`` that records every call."""

    def __init__(self, key=None):
        self.key = key
        self.calls: list[dict[str, Any]] = []

    def directions(self, **kwargs):
        self.calls.append(kwargs)
        fmt = kwargs.get("format")
        if fmt == "gpx":
            return "<gpx></gpx>"
        # Minimal GeoJSON shape the production code reads.
        return {
            "features": [
                {
                    "properties": {
                        "summary": {
                            "distance": 1234.5,
                            "ascent": 67.8,
                            "duration": 909,
                        }
                    },
                    "geometry": {
                        "coordinates": [[13.4, 52.5], [13.41, 52.51]],
                    },
                }
            ]
        }


@pytest.fixture
def manager(monkeypatch):
    """A RoutingManager pre-wired with a fake ORS client."""
    monkeypatch.setenv("ORS_API_KEY", "test-key")
    m = RoutingManager()
    m.client = _FakeOrsClient(key="test-key")
    return m


class TestProfileAwareAvoidFeatures:
    """ORS error 2003: ``highways`` is invalid for cycling/foot profiles."""

    def test_cycling_mountain_drops_highways(self):
        assert "highways" not in _filter_avoid_features(
            "cycling-mountain", ["highways", "ferries"]
        )

    def test_cycling_mountain_keeps_steps(self):
        assert "steps" in _filter_avoid_features(
            "cycling-mountain", ["steps", "highways"]
        )

    def test_driving_car_keeps_highways(self):
        assert "highways" in _filter_avoid_features(
            "driving-car", ["highways", "tollways"]
        )

    def test_foot_hiking_drops_highways(self):
        assert "highways" not in _filter_avoid_features(
            "foot-hiking", ["highways", "ferries"]
        )

    def test_build_options_mtb_with_prefer_trails_has_no_highways(self, manager):
        """The MTB + prefer_trails combo that originally produced
        ``error 2003: avoid_features - highways is not valid with profile
        - cycling-mountain`` must never put ``highways`` on the wire."""
        opts = manager._build_options(
            "cycling-mountain", prefer_trails=True, avoid_roads=False,
        )
        assert opts is not None
        assert "highways" not in opts.get("avoid_features", [])

    def test_build_options_mtb_with_avoid_roads_has_no_highways(self, manager):
        opts = manager._build_options(
            "cycling-mountain", prefer_trails=False, avoid_roads=True,
        )
        if opts is not None:
            assert "highways" not in opts.get("avoid_features", [])

    def test_build_options_driving_with_avoid_roads_keeps_highways(self, manager):
        opts = manager._build_options(
            "driving-car", prefer_trails=False, avoid_roads=True,
        )
        assert opts is not None
        assert "highways" in opts["avoid_features"]


class TestCoordOrderAndSnapping:
    """ORS error 2010: ``Could not find routable point within 350m``."""

    def test_to_lon_lat_swaps_pair(self):
        # Karlsruhe Hauptbahnhof ~ (49.0094, 8.4001) in lat/lon order;
        # ORS expects [8.4001, 49.0094].
        out = _to_lon_lat((49.0094, 8.4001))
        assert out == [8.4001, 49.0094]

    def test_to_lon_lat_accepts_list_with_altitude(self):
        out = _to_lon_lat([49.0094, 8.4001, 120.0])
        assert out == [8.4001, 49.0094]

    def test_plan_route_sends_lon_lat_to_ors(self, manager):
        # Inputs are ``(lat, lon)`` per the tool layer's contract.
        start = (49.0094, 8.4001)
        end = (49.0136, 8.4045)
        manager.plan_route(start=start, end=end, sport="hike")

        # Two calls expected: geojson + gpx, both with the same coords.
        assert len(manager.client.calls) == 2
        for call in manager.client.calls:
            coords = call["coordinates"]
            assert coords[0] == [8.4001, 49.0094]
            assert coords[-1] == [8.4045, 49.0136]

    def test_plan_route_extends_snap_radius(self, manager):
        start = (49.0094, 8.4001)
        end = (49.0136, 8.4045)
        manager.plan_route(start=start, end=end, sport="hike")

        # ``radiuses`` should be present and at least the configured
        # default per waypoint — the bug was the default 350m being too
        # tight for geocoder hits.
        for call in manager.client.calls:
            radii = call.get("radiuses")
            assert radii is not None
            assert len(radii) == len(call["coordinates"])
            assert all(r >= _DEFAULT_SNAP_RADIUS_M for r in radii)

    def test_plan_route_mtb_prefer_trails_does_not_send_highways(self, manager):
        # End-to-end: the MTB + prefer_trails case that triggered the
        # production bug must not put ``highways`` on the wire.
        start = (49.0094, 8.4001)
        end = (49.0136, 8.4045)
        manager.plan_route(
            start=start, end=end, sport="mountain_bike", prefer_trails=True,
        )
        for call in manager.client.calls:
            options = call.get("options") or {}
            assert "highways" not in options.get("avoid_features", [])
