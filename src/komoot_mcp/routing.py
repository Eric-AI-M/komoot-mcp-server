import os
import openrouteservice

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
        self.client = openrouteservice.Client(key=key)

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
            coords = [start]
            options = self._build_options(profile, prefer_trails, avoid_roads) or {}
            options["round_trip"] = {
                "length": int(target_distance_km * 1000),
                "points": 3,
                "seed": 42
            }
        else:
            if not end:
                raise RoutingError("end point is required for point-to-point routing")
            coords = [start]
            if waypoints:
                coords.extend(waypoints)
            coords.append(end)
            options = self._build_options(profile, prefer_trails, avoid_roads)

        try:
            # Get directions
            result = self.client.directions(
                coordinates=coords,
                profile=profile,
                format="geojson",
                options=options,
                instructions=True,
                elevation=True,
                extra_info=["surface", "waytype"],
            )

            # Request GPX separately
            gpx_result = self.client.directions(
                coordinates=coords,
                profile=profile,
                format="gpx",
                options=options,
                elevation=True,
            )
        except openrouteservice.exceptions.ApiError as e:
            raise RoutingError(f"OpenRouteService error: {e}")
        except Exception as e:
            raise RoutingError(f"Route planning failed: {e}")

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
