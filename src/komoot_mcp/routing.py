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

class RoutingManager:
    def __init__(self):
        api_key = os.environ.get("ORS_API_KEY")
        if not api_key:
            raise RoutingError("ORS_API_KEY environment variable must be set")
        self.client = openrouteservice.Client(key=api_key)

    def _build_options(self, sport, prefer_trails, avoid_roads):
        avoid_features = []
        if prefer_trails:
            avoid_features.append("highways")
        if avoid_roads:
            # ORS only accepts a fixed enum for avoid_features. "secondary"
            # is not in the spec — adding it makes the API reject the
            # whole request with HTTP 400.
            avoid_features.append("highways")
        options = {}
        if avoid_features:
            options["avoid_features"] = list(set(avoid_features))
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
            options = self._build_options(sport, prefer_trails, avoid_roads) or {}
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
            options = self._build_options(sport, prefer_trails, avoid_roads)

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
