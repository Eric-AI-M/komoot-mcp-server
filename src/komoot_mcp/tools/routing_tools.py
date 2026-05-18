"""Routing tools for Komoot MCP server."""

from komoot_mcp.context import get_client, get_geocoder, get_routing_manager
from komoot_mcp.tools.data_tools import _format_gpx_response


# Map our public sport identifiers (used by komoot_plan_route) to the
# Komoot activity strings (used by komoot_upload_tour). The two
# vocabularies diverged historically — ``mountain_bike`` on the routing
# side, ``mtb`` on the Komoot side, etc. Keeping the map narrow on
# purpose: when a sport isn't mapped explicitly we pass through
# ``touringbicycle`` so the upload still succeeds with a sensible default.
_SPORT_TO_KOMOOT_ACTIVITY = {
    "hike": "hike",
    "trail_run": "jogging",
    "mountain_bike": "mtb",
    "road_cycle": "racebike",
    "gravel_ride": "mtb_easy",
}


def _komoot_activity_for(sport: str) -> str:
    return _SPORT_TO_KOMOOT_ACTIVITY.get(sport, "touringbicycle")


def register(mcp):
    @mcp.tool()
    async def komoot_geocode(query: str, limit: int = 5) -> str:
        """Geocode a place name to coordinates using Komoot's Photon API (free, no key needed).

        Args:
            query: Place name, address, or coordinates (e.g. 'Berlin', 'Marienplatz Munich', '52.52,13.40')
            limit: Max number of results (default 5)
        """
        try:
            geocoder = get_geocoder()
            parts = query.split(",")
            if len(parts) == 2:
                try:
                    lat = float(parts[0].strip())
                    lon = float(parts[1].strip())
                    result = geocoder.reverse(lat, lon)
                    return (
                        f"Location: {result.get('display_name', 'unknown')}\n"
                        f"  City: {result.get('city', '?')}\n"
                        f"  Country: {result.get('country', '?')}\n"
                        f"  Coordinates: {result['lat']}, {result['lon']}\n"
                        f"  Type: {result.get('type', '?')}"
                    )
                except ValueError:
                    pass

            results = geocoder.forward(query, limit)
            if not results:
                return f"No locations found for '{query}'"
            lines = [f"Geocoding results for '{query}':"]
            for i, r in enumerate(results):
                lines.append(
                    f"  [{i}] {r['display_name']} ({r['city']}, {r['country']}) | "
                    f"{r['lat']}, {r['lon']} | type: {r['type']}"
                )
            return "\n".join(lines)
        except Exception as e:
            return f"Geocoding error: {e}"

    @mcp.tool()
    async def komoot_plan_route(
        start: str,
        end: str = None,
        roundtrip: bool = False,
        target_distance_km: float = None,
        sport: str = "hike",
        prefer_trails: bool = False,
        avoid_roads: bool = False,
        waypoints: str = None,
    ) -> str:
        """Plan a route using OpenRouteService with sport-specific profiles.

        Use this to create routes with preferences like "maximize trails, minimize roads".
        The planned route can then be uploaded to Komoot with komoot_upload_tour.

        Args:
            start: Starting point — place name or 'lat,lng'
            end: End point — place name or 'lat,lng' (omit for roundtrip)
            roundtrip: If True, creates a loop starting and ending at start
            target_distance_km: Target distance in kilometers (required for roundtrip)
            sport: One of 'hike', 'trail_run', 'mountain_bike', 'road_cycle', 'gravel_ride'
            prefer_trails: If True, maximize trails and paths, avoid highways
            avoid_roads: If True, minimize paved roads
            waypoints: Comma-separated coordinates e.g. "52.5,13.4|52.6,13.5"
        """
        routing = get_routing_manager()
        if routing is None:
            return (
                "Error: ORS API key not configured for this org. "
                "Add it in the dashboard under Komoot credentials "
                "(free signup at https://openrouteservice.org/dev/#/signup)."
            )

        try:
            geocoder = get_geocoder()
            start_coords = _parse_location(start, geocoder)
            if not start_coords:
                return f"Could not geocode start location: {start}"

            end_coords = None
            if end:
                end_coords = _parse_location(end, geocoder)
                if not end_coords:
                    return f"Could not geocode end location: {end}"

            waypoint_coords = None
            if waypoints:
                waypoint_coords = []
                for wp_str in waypoints.split("|"):
                    wp = _parse_coords(wp_str.strip())
                    if wp:
                        waypoint_coords.append(wp)

            result = routing.plan_route(
                start=start_coords,
                end=end_coords,
                roundtrip=roundtrip,
                target_distance_km=target_distance_km,
                sport=sport,
                prefer_trails=prefer_trails,
                avoid_roads=avoid_roads,
                waypoints=waypoint_coords,
            )

            # Issue #9: GPX content is returned inline so the caller can
            # use it directly. Server-side filesystem paths are useless
            # under the multi-tenant gateway.
            summary = (
                f"Route planned successfully!\n"
                f"  Distance: {result['distance_km']} km\n"
                f"  Elevation gain: {result['elevation_gain_m']} m\n"
                f"  Estimated duration: {result['duration_minutes']} min\n"
                f"  Sport profile: {sport}\n"
                f"  Waypoints: {len(result['waypoints'])} points\n\n"
            )
            gpx_block = _format_gpx_response(
                f"planned {sport} route", result["gpx"],
            )
            return summary + gpx_block
        except Exception as e:
            return f"Route planning failed: {e}"

    @mcp.tool()
    async def komoot_plan_and_upload(
        start: str,
        end: str = None,
        roundtrip: bool = False,
        target_distance_km: float = None,
        sport: str = "hike",
        prefer_trails: bool = False,
        avoid_roads: bool = False,
        waypoints: str = None,
        tour_name: str = None,
    ) -> str:
        """Plan a route via OpenRouteService and upload it to Komoot in
        one server-side operation. Returns the Komoot tour ID and URL.

        Use this when you want to add a planned route to your Komoot
        tours without round-tripping the GPX (typically 300–500 KB,
        ~100k tokens) through the conversation. The GPX is generated
        and uploaded entirely on the server.

        Args:
            start: Starting point — place name or 'lat,lng'
            end: End point — place name or 'lat,lng' (omit for roundtrip)
            roundtrip: If True, creates a loop starting and ending at start
            target_distance_km: Target distance in kilometers (required for roundtrip)
            sport: One of 'hike', 'trail_run', 'mountain_bike',
                'road_cycle', 'gravel_ride'. Maps to the matching Komoot
                activity type for the uploaded tour.
            prefer_trails: If True, maximize trails and paths, avoid highways
            avoid_roads: If True, minimize paved roads
            waypoints: Comma-separated coordinates e.g. "52.5,13.4|52.6,13.5"
            tour_name: Display name for the uploaded tour (optional).
                Defaults to "Planned <sport> route".
        """
        routing = get_routing_manager()
        if routing is None:
            return (
                "Error: ORS API key not configured for this org. "
                "Add it in the dashboard under Komoot credentials "
                "(free signup at https://openrouteservice.org/dev/#/signup)."
            )

        # --- Step 1: plan the route (same logic as komoot_plan_route) ---
        try:
            geocoder = get_geocoder()
            start_coords = _parse_location(start, geocoder)
            if not start_coords:
                return f"Could not geocode start location: {start}"

            end_coords = None
            if end:
                end_coords = _parse_location(end, geocoder)
                if not end_coords:
                    return f"Could not geocode end location: {end}"

            waypoint_coords = None
            if waypoints:
                waypoint_coords = []
                for wp_str in waypoints.split("|"):
                    wp = _parse_coords(wp_str.strip())
                    if wp:
                        waypoint_coords.append(wp)

            plan = routing.plan_route(
                start=start_coords,
                end=end_coords,
                roundtrip=roundtrip,
                target_distance_km=target_distance_km,
                sport=sport,
                prefer_trails=prefer_trails,
                avoid_roads=avoid_roads,
                waypoints=waypoint_coords,
            )
        except Exception as e:
            return f"Route planning failed: {e}"

        # --- Step 2: upload to Komoot, capturing the new tour ID ---
        gpx = plan["gpx"]
        name = tour_name or f"Planned {sport} route"
        activity = _komoot_activity_for(sport)
        try:
            result = await get_client().upload_gpx_capture_id(
                gpx_content=gpx,
                sport=activity,
                tour_name=name,
                # The user's intent is "save this planned route to
                # Komoot", not "log an activity I just did". Explicit
                # here even though it's the helper's default, so the
                # intent is grep-able from this call site.
                tour_type="tour_planned",
            )
        except Exception as e:
            return (
                f"Route planned ({plan['distance_km']} km, "
                f"{plan['elevation_gain_m']} m climb) but upload to "
                f"Komoot failed: {e}\n"
                f"Try komoot_plan_route alone to inspect the GPX, then "
                f"komoot_upload_tour to retry the upload."
            )

        tid = result.get("id")
        status = result.get("status", "uploaded")
        lines = [
            f"Route planned and uploaded to Komoot.",
            f"  Distance: {plan['distance_km']} km",
            f"  Elevation gain: {plan['elevation_gain_m']} m",
            f"  Estimated duration: {plan['duration_minutes']} min",
            f"  Sport: {sport} (Komoot activity: {activity})",
            f"  Status: {status}",
        ]
        if tid:
            lines.append(f"  Tour ID: {tid}")
            lines.append(f"  URL: https://www.komoot.com/tour/{tid}")
        else:
            lines.append(
                "  Tour ID: <not returned by Komoot> — check your tours list."
            )
        return "\n".join(lines)


def _parse_location(s: str, geocoder):
    coords = _parse_coords(s)
    if coords:
        return coords
    results = geocoder.forward(s, limit=1)
    if results:
        r = results[0]
        return (r["lat"], r["lon"])
    return None


def _parse_coords(s: str):
    parts = s.split(",")
    if len(parts) == 2:
        try:
            lat = float(parts[0].strip())
            lon = float(parts[1].strip())
            return (lat, lon)
        except ValueError:
            pass
    return None
