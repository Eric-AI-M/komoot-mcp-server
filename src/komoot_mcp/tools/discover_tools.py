"""Discovery tools for Komoot MCP server.

Phase 2 added ``komoot_recommend_tours_near`` (wraps the
``/v007/discover/{lat,lng}/elements/`` umbrella endpoint). Phase 3
extends with Smart Tour suggestions, attribute-filtered discovery, the
Komoot search service, and Trailview photo lookup. Several of the
Phase 3 tools hit endpoints whose URL shapes were inferred from JS-
bundle scans rather than live-probed — each one is flagged
EXPERIMENTAL in its docstring.
"""

from komoot_mcp.context import get_client


def _items(data):
    """Pull a list of items out of a HAL response (best effort)."""
    if not isinstance(data, dict):
        return []
    emb = data.get("_embedded")
    if isinstance(emb, dict):
        items = emb.get("items")
        if isinstance(items, list):
            return items
        for v in emb.values():
            if isinstance(v, list):
                return v
    for key in ("items", "results", "content"):
        v = data.get(key)
        if isinstance(v, list):
            return v
    return []


def _render_tour_item(item):
    """Render one discover/smart-tour item as a single line."""
    if not isinstance(item, dict):
        return None
    name = item.get("name") or item.get("title") or "unnamed"
    tid = item.get("id") or item.get("tour_id") or "?"
    sport = item.get("sport") or item.get("sports") or "?"
    distance = item.get("distance")
    elev = item.get("elevation_up")
    bits = [f"[{tid}] {name} | sport={sport}"]
    if isinstance(distance, (int, float)):
        bits.append(f"{distance / 1000:.1f} km")
    if isinstance(elev, (int, float)):
        bits.append(f"+{int(elev)}m")
    return "  " + " | ".join(bits)


def register(mcp):
    @mcp.tool()
    async def komoot_recommend_tours_near(
        lat: float,
        lng: float,
        sport: str = None,
        radius_km: float = 20,
        limit: int = 10,
    ) -> str:
        """Recommend tours, smart tours, and collections near a point.

        Wraps Komoot's discovery surface
        (``/v007/discover/{lat,lng}/elements/``). Returns up to ``limit``
        items, each with name, type, sport, distance, and a share URL
        when one is exposed.

        Args:
            lat: Latitude of the center point
            lng: Longitude of the center point
            sport: Optional sport filter (e.g. 'hike', 'touringbicycle',
                'mountainbike', 'racebike'). When omitted, all sports
                are returned.
            radius_km: Search radius (forwarded as a hint; Komoot may
                clamp or ignore). Default 20 km.
            limit: Max items to return (default 10).
        """
        try:
            data = await get_client().discover_near(
                lat=lat, lng=lng, sport=sport, limit=limit,
            )
        except Exception as e:
            return f"Error discovering tours: {e}"

        items = []
        if isinstance(data, dict):
            emb = data.get("_embedded") or {}
            if isinstance(emb, dict):
                items = emb.get("items") or []
            if not items:
                items = data.get("items") or []

        if not items:
            return (
                f"No discoverable tours near ({lat}, {lng}). "
                "Try a different point or remove the sport filter."
            )

        header = f"Discovered {len(items)} items near ({lat}, {lng})"
        if sport:
            header += f" filtered by sport={sport}"
        lines = [header + ":"]

        for i, item in enumerate(items[:limit]):
            if not isinstance(item, dict):
                continue
            kind = item.get("type") or item.get("item_type") or "?"
            name = item.get("name") or item.get("title")
            sport_str = item.get("sport") or item.get("sports") or "?"
            distance = item.get("distance")

            sub_emb = item.get("_embedded") or {}
            if isinstance(sub_emb, dict):
                main = sub_emb.get("main_tour")
                if isinstance(main, dict):
                    name = name or main.get("name")
                    sport_str = (
                        sport_str if sport_str != "?" else main.get("sport") or "?"
                    )
                    distance = distance if distance is not None else main.get("distance")

            url = None
            links = item.get("_links") or {}
            if isinstance(links, dict):
                self_link = links.get("self")
                if isinstance(self_link, dict):
                    href = self_link.get("href")
                    if isinstance(href, str) and href.startswith("/"):
                        url = "https://www.komoot.com" + href
                    elif isinstance(href, str):
                        url = href
            if not url:
                tid = item.get("id")
                if isinstance(sub_emb, dict):
                    main = sub_emb.get("main_tour")
                    if isinstance(main, dict) and main.get("id"):
                        tid = main["id"]
                if tid:
                    url = f"https://www.komoot.com/tour/{tid}"

            name = name or "unnamed"
            bits = [f"[{i}] {kind} | {name} | sport={sport_str}"]
            if isinstance(distance, (int, float)):
                bits.append(f"{distance / 1000:.1f} km")
            line = "  " + " | ".join(bits)
            if url:
                line += f"\n      {url}"
            lines.append(line)

        return "\n".join(lines)

    @mcp.tool()
    async def komoot_smart_tours_near(
        lat: float,
        lng: float,
        sport: str,
        radius_km: float = 20,
        limit: int = 10,
    ) -> str:
        """Recommend Smart Tours near a point (EXPERIMENTAL).

        Tries the dedicated Smart Tour API host first
        (``smarttour-api.main.komoot.net/api/v1``), then falls back to
        ``/v007/smart_tours/``. Endpoint shape inferred from the JS
        bundle — verify on first live call.

        Args:
            lat: Latitude
            lng: Longitude
            sport: Sport profile (e.g. 'hike', 'touringbicycle',
                'mountainbike', 'racebike')
            radius_km: Search radius (default 20 km)
            limit: Max items to return (default 10)
        """
        try:
            data = await get_client().smart_tours_near(
                lat, lng, sport, radius_km=radius_km, limit=limit,
            )
        except Exception as e:
            return f"Error getting smart tours: {e}"

        items = _items(data)
        if not items:
            return (
                f"No smart tours near ({lat}, {lng}) for sport={sport}. "
                "Try a different point or wider radius."
            )
        lines = [
            f"Smart Tours near ({lat}, {lng}) for sport={sport} "
            f"(radius {radius_km}km, {len(items)} found):"
        ]
        for it in items[:limit]:
            line = _render_tour_item(it)
            if line:
                lines.append(line)
        return "\n".join(lines)

    @mcp.tool()
    async def komoot_smart_tour_for_highlight(
        highlight_id: int, sport: str = None,
    ) -> str:
        """Suggested tours that pass through a highlight (POI).

        Args:
            highlight_id: The numeric highlight ID
            sport: Optional sport filter
        """
        try:
            data = await get_client().smart_tour_for_highlight(
                highlight_id, sport=sport,
            )
        except Exception as e:
            return f"Error getting smart tour for highlight: {e}"

        items = _items(data)
        if not items:
            return f"No suggested tours for highlight {highlight_id}."
        lines = [
            f"Suggested tours for highlight {highlight_id} "
            f"({len(items)} found):"
        ]
        for it in items[:20]:
            line = _render_tour_item(it)
            if line:
                lines.append(line)
        return "\n".join(lines)

    @mcp.tool()
    async def komoot_smart_tour_for_region(
        region_id: str, sport: str = None,
    ) -> str:
        """Suggested tours inside a region.

        Args:
            region_id: Komoot region identifier (string)
            sport: Optional sport filter
        """
        try:
            data = await get_client().smart_tour_for_region(
                region_id, sport=sport,
            )
        except Exception as e:
            return f"Error getting smart tour for region: {e}"

        items = _items(data)
        if not items:
            return f"No suggested tours for region {region_id}."
        lines = [
            f"Suggested tours for region {region_id} ({len(items)} found):"
        ]
        for it in items[:20]:
            line = _render_tour_item(it)
            if line:
                lines.append(line)
        return "\n".join(lines)

    @mcp.tool()
    async def komoot_discover_with_attributes(
        lat: float,
        lng: float,
        sport: str = None,
        attributes: str = None,
    ) -> str:
        """Discover tours near a point, filtered by route attributes.

        Route attributes are tags like ``scenic``, ``challenging``,
        ``family_friendly``. Use ``komoot_route_attribute_options`` to
        enumerate the legal values.

        Args:
            lat: Latitude
            lng: Longitude
            sport: Optional sport filter
            attributes: Comma-separated attribute names (e.g.
                ``scenic,challenging``)
        """
        try:
            data = await get_client().discover_with_attributes(
                lat, lng, sport=sport, attributes=attributes,
            )
        except Exception as e:
            return f"Error discovering tours by attributes: {e}"

        items = _items(data)
        if not items:
            return (
                f"No tours match attributes={attributes!r} near "
                f"({lat}, {lng})."
            )
        header = (
            f"Discovered {len(items)} tours near ({lat}, {lng})"
        )
        if attributes:
            header += f" with attributes={attributes}"
        if sport:
            header += f" sport={sport}"
        lines = [header + ":"]
        for it in items[:20]:
            line = _render_tour_item(it)
            if line:
                lines.append(line)
        return "\n".join(lines)

    @mcp.tool()
    async def komoot_route_attribute_options() -> str:
        """List the legal route-attribute names accepted by discovery.

        Use these in ``komoot_discover_with_attributes``.
        """
        try:
            data = await get_client().route_attribute_options()
        except Exception as e:
            return f"Error getting route attributes: {e}"

        items = _items(data)
        if not items and isinstance(data, dict):
            items = list(data.keys())

        if not items:
            return f"No route attributes returned. Raw shape: {type(data).__name__}"
        lines = [f"Route attributes ({len(items)} options):"]
        for it in items:
            if isinstance(it, dict):
                name = it.get("name") or it.get("key") or it.get("id") or "?"
                label = it.get("label") or ""
                lines.append(f"  - {name}: {label}".rstrip(": "))
            else:
                lines.append(f"  - {it}")
        return "\n".join(lines)

    @mcp.tool()
    async def komoot_search(
        query: str,
        kind: str = "tour",
        sport: str = None,
        near: str = None,
        limit: int = 10,
    ) -> str:
        """Search Komoot (EXPERIMENTAL).

        Hits ``search-api.main.komoot.net/v1/search``. Endpoint shape
        inferred from JS-bundle scans — verify on first live call.

        Args:
            query: Search query string
            kind: Result type ('tour', 'highlight', 'region', 'user', etc.)
            sport: Optional sport filter
            near: Optional location bias as 'lat,lng' string
            limit: Max results (default 10)
        """
        near_tuple = None
        if near:
            parts = [p.strip() for p in near.split(",")]
            if len(parts) == 2:
                try:
                    near_tuple = (float(parts[0]), float(parts[1]))
                except ValueError:
                    near_tuple = near
            else:
                near_tuple = near

        try:
            data = await get_client().search(
                query, kind=kind, sport=sport, near=near_tuple, limit=limit,
            )
        except Exception as e:
            return f"Error searching Komoot: {e}"

        items = _items(data)
        if not items:
            return f"No results for query={query!r} kind={kind!r}."

        lines = [f"Komoot search results for {query!r} kind={kind} ({len(items)}):"]
        for it in items[:limit]:
            if not isinstance(it, dict):
                continue
            rid = it.get("id") or "?"
            name = it.get("name") or it.get("title") or "?"
            sport_str = it.get("sport") or it.get("sports") or ""
            line = f"  [{rid}] {name}"
            if sport_str:
                line += f" | sport={sport_str}"
            lines.append(line)
        return "\n".join(lines)

    @mcp.tool()
    async def komoot_get_trailview(
        lat: float, lng: float, radius_m: int = 500,
    ) -> str:
        """Get Komoot Trailview photos near a point (EXPERIMENTAL).

        Hits ``trailview-api.maps.komoot.net/api/v1/photos``. Endpoint
        shape inferred from the JS-bundle subdomain scan — verify on
        first live call.

        Args:
            lat: Latitude
            lng: Longitude
            radius_m: Search radius in metres (default 500)
        """
        try:
            data = await get_client().get_trailview(lat, lng, radius_m=radius_m)
        except Exception as e:
            return f"Error getting trailview: {e}"

        items = _items(data)
        if not items:
            return (
                f"No Trailview photos near ({lat}, {lng}). "
                f"Raw response keys: "
                f"{list(data.keys()) if isinstance(data, dict) else type(data).__name__}"
            )
        lines = [
            f"Trailview photos near ({lat}, {lng}) within {radius_m}m "
            f"({len(items)}):"
        ]
        for it in items[:20]:
            if not isinstance(it, dict):
                continue
            pid = it.get("id", "?")
            url = it.get("url") or it.get("src") or ""
            dist = it.get("distance")
            line = f"  [{pid}] {url}"
            if dist is not None:
                line += f" ({dist}m away)"
            lines.append(line)
        return "\n".join(lines)
