import json
import os

import openrouteservice
import requests


class RoutingError(Exception):
    pass

SPORT_PROFILES = {
    "hike": "foot-hiking",
    "trail_run": "foot-walking",
    "mountain_bike": "cycling-mountain",
    "road_cycle": "cycling-road",
    "gravel_ride": "cycling-regular",
}

# ORS only allows specific avoid_features values per profile family. Sending
# the wrong one (e.g. "highways" on cycling-mountain) causes a request-wide
# 400 with error 2003. Source: ORS docs — routing-options.md.
# https://github.com/giscience/openrouteservice/blob/main/docs/api-reference/endpoints/directions/routing-options.md
_AVOID_FEATURES_BY_FAMILY = {
    "driving": {"highways", "tollways", "ferries"},
    "cycling": {"steps", "ferries", "fords"},
    "foot": {"ferries", "fords", "steps"},
}

# Snap-radius default in ORS is 350m, which is too tight for geocoder hits
# that land off-network (e.g. atop a station's building footprint). Extend
# to 1km per waypoint to cover the common cases the user hit.
_DEFAULT_SNAP_RADIUS_M = 1000


def _profile_family(profile: str) -> str:
    """Map an ORS profile name to its avoid-features family."""
    if profile.startswith("driving"):
        return "driving"
    if profile.startswith("cycling"):
        return "cycling"
    if profile.startswith("foot"):
        return "foot"
    # Unknown family — default to the most restrictive (foot).
    return "foot"


def _filter_avoid_features(profile: str, requested: list[str]) -> list[str]:
    """Drop avoid-features values the given profile doesn't accept."""
    allowed = _AVOID_FEATURES_BY_FAMILY.get(_profile_family(profile), set())
    return [f for f in requested if f in allowed]


def _to_lon_lat(coord):
    """Convert an internal ``(lat, lon)`` coord to the ``[lon, lat]`` ORS
    expects. Accepts tuple/list of length 2+ (alt is ignored). The whole
    server stores geocoded points as ``(lat, lon)`` — historically the
    routing layer forwarded those unchanged to ORS, which produced
    400/error-2010 "Could not find routable point" because ORS searched
    for the network in the wrong hemisphere/region.
    """
    if coord is None:
        return None
    lat, lon = coord[0], coord[1]
    return [float(lon), float(lat)]


class RoutingManager:
    def __init__(self, api_key: str | None = None):
        # Per-request key wins; fall back to env for stdio/local-dev so
        # single-user setups keep working without the platform gateway.
        key = api_key or os.environ.get("ORS_API_KEY")
        if not key:
            raise RoutingError(
                "ORS API key not configured for this org. Add it in the "
                "dashboard under Komoot credentials (free signup at "
                "https://openrouteservice.org/dev/#/signup), or set "
                "ORS_API_KEY when running in stdio mode."
            )
        self._key = key
        self.client = openrouteservice.Client(key=key)

    def _fetch_gpx(self, *, profile, coordinates, options, radiuses):
        """POST to ORS ``directions/{profile}/gpx`` and return XML text.

        Issue #11: the ``openrouteservice`` Python client routes every
        response through ``_get_body``, which calls ``response.json()``.
        For ``format="gpx"`` the body is XML, so ``json.JSONDecodeError``
        fires and the client raises ``HTTPError(response.status_code)``
        — i.e. ``HTTP Error: 200`` on a perfectly successful request.
        Our previous ``except Exception`` block surfaced that as
        "Route planning failed: HTTP Error: 200" to the user.

        We sidestep the client and POST ourselves for the GPX format
        only; the GeoJSON call (which IS valid JSON) still goes through
        ``client.directions``.
        """
        body: dict = {"coordinates": coordinates}
        if options:
            body["options"] = options
        if radiuses:
            body["radiuses"] = radiuses
        body["elevation"] = True

        url = (
            f"{self.client._base_url}/v2/directions/{profile}/gpx"
        )
        headers = {
            "Authorization": self._key,
            "Content-Type": "application/json",
            "Accept": "application/gpx+xml, application/xml",
        }
        try:
            resp = requests.post(
                url,
                data=json.dumps(body),
                headers=headers,
                timeout=self.client._timeout,
            )
        except requests.exceptions.Timeout as e:
            raise RoutingError(f"OpenRouteService timed out fetching GPX: {e}")
        except requests.exceptions.RequestException as e:
            raise RoutingError(f"OpenRouteService transport error: {e}")

        if resp.status_code != 200:
            # Try to surface a useful body (ORS returns JSON for errors
            # even when GPX was requested).
            try:
                err = resp.json()
            except ValueError:
                err = resp.text[:500]
            raise RoutingError(
                f"OpenRouteService GPX request failed "
                f"(status {resp.status_code}): {err}"
            )
        return resp.text

    def _build_options(self, profile, prefer_trails, avoid_roads):
        # Both prefer_trails and avoid_roads previously appended
        # "highways", which ORS rejects for any cycling-* or foot-*
        # profile. Build a profile-aware avoid_features list instead.
        # For cycling we add "steps" (closest analog to "no rough
        # stairs") for prefer_trails; "fords" for avoid_roads can be
        # debated — leaving avoid_roads as a no-op on cycling/foot
        # rather than silently sending an unsupported feature.
        requested: list[str] = []
        family = _profile_family(profile)
        if prefer_trails:
            if family == "driving":
                # Drivers wanting "trails" really mean "avoid highways".
                requested.append("highways")
            elif family == "cycling":
                # Bikers wanting trails want to avoid steps & fords.
                requested.append("steps")
            # foot-* has no good "prefer trails" toggle in avoid_features.
        if avoid_roads:
            if family == "driving":
                requested.append("highways")
            # On cycling/foot, "avoid_roads" doesn't map to any ORS
            # avoid_features value — leave the request open rather than
            # ship an invalid one. Prefer_trails covers the steps case.
        avoid_features = _filter_avoid_features(profile, list(set(requested)))
        options = {}
        if avoid_features:
            options["avoid_features"] = avoid_features
        return options if options else None

    def plan_route(self, start, end=None, roundtrip=False, target_distance_km=None,
                   sport="hike", prefer_trails=False, avoid_roads=False, waypoints=None):
        profile = SPORT_PROFILES.get(sport)
        if not profile:
            raise RoutingError(f"Unknown sport: {sport}. Valid: {list(SPORT_PROFILES.keys())}")

        if roundtrip:
            if not target_distance_km:
                raise RoutingError("target_distance_km is required for roundtrip routing")
            coords = [_to_lon_lat(start)]
            options = self._build_options(profile, prefer_trails, avoid_roads) or {}
            options["round_trip"] = {
                "length": int(target_distance_km * 1000),
                "points": 3,
                "seed": 42
            }
        else:
            if not end:
                raise RoutingError("end point is required for point-to-point routing")
            coords = [_to_lon_lat(start)]
            if waypoints:
                coords.extend(_to_lon_lat(wp) for wp in waypoints)
            coords.append(_to_lon_lat(end))
            options = self._build_options(profile, prefer_trails, avoid_roads)

        # Extend ORS's snap radius per waypoint. Default 350m is too tight
        # for geocoder hits that land on building footprints; 1km covers
        # the named-place + lat/lng failures the user reported (error 2010
        # "Could not find routable point within a radius of 350.0 meters").
        radiuses = [_DEFAULT_SNAP_RADIUS_M] * len(coords)

        try:
            # GeoJSON for summary + parsed coords; JSON shape is what the
            # vendored ORS client expects, so this path is unchanged.
            result = self.client.directions(
                coordinates=coords,
                profile=profile,
                format="geojson",
                options=options,
                instructions=True,
                elevation=True,
                extra_info=["surface", "waytype"],
                radiuses=radiuses,
            )
        except openrouteservice.exceptions.ApiError as e:
            raise RoutingError(f"OpenRouteService error: {e}")
        except openrouteservice.exceptions.HTTPError as e:
            raise RoutingError(f"OpenRouteService transport error: {e}")
        except RoutingError:
            raise
        except Exception as e:
            raise RoutingError(f"Route planning failed: {e}")

        # GPX format is XML — the ORS client unconditionally calls
        # ``response.json()`` on it and raises ``HTTPError(200)``. Fetch
        # it ourselves over plain HTTP. See ``_fetch_gpx`` for the long
        # version of why.
        gpx_result = self._fetch_gpx(
            profile=profile,
            coordinates=coords,
            options=options,
            radiuses=radiuses,
        )

        feature = result["features"][0]
        props = feature["properties"]
        summary = props.get("summary", {})

        route_coords = feature["geometry"]["coordinates"]

        return {
            "gpx": gpx_result,
            "distance_km": round(summary.get("distance", 0) / 1000, 2),
            "elevation_gain_m": round(summary.get("ascent", 0), 1),
            "duration_minutes": round(summary.get("duration", 0) / 60, 1),
            "waypoints": [(c[1], c[0]) for c in route_coords],  # lon,lat → lat,lon
        }
