"""Discovery tools for Komoot MCP server.

Surfaces Komoot's "what's near here?" recommendation engine. The
underlying ``/v007/discover/{lat,lng}/elements/`` endpoint returns a
mix of editorial collections, smart tours and suggested tours under
``_embedded.items`` — we normalise that into a single LLM-friendly
list.
"""

from komoot_mcp.context import get_client


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
