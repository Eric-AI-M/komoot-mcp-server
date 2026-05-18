"""Browse and search tools for Komoot MCP server."""

from komoot_mcp.context import get_client


def register(mcp):
    @mcp.tool()
    async def komoot_list_tours(
        page: int = 0,
        limit: int = 50,
        sport_type: str = None,
        status: str = None,
        name: str = None,
        sort_field: str = "date",
        sort_direction: str = "desc",
    ) -> str:
        """List your Komoot tours with filters.

        Args:
            page: Page number (0-indexed)
            limit: Results per page (max 50)
            sport_type: Filter by sport (e.g. 'hike', 'touringbicycle', 'mountainbike', 'racebike', 'run')
            status: Filter by visibility ('public', 'private', 'friends')
            name: Search by tour name (case-insensitive substring)
            sort_field: Sort by ('date', 'name', 'elevation', 'duration')
            sort_direction: Sort order ('asc' or 'desc')
        """
        try:
            result = await get_client().list_tours(
                page=page, limit=limit, sport_type=sport_type,
                status=status, name=name, sort_field=sort_field,
                sort_direction=sort_direction,
            )
            tours = result.get("tours", [])
            if not tours:
                return "No tours found."
            lines = [f"Tours (page {page}, {len(tours)} results):"]
            for t in tours:
                dist = t.get('distance', '?')
                elev = t.get('elevation_up', '?')
                sport = t.get('sport', '?')
                status_str = t.get('status', '?')
                lines.append(
                    f"  [{t['id']}] {t.get('name', 'unnamed')} | {sport} | {status_str} | {dist}m | +{elev}m"
                )
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing tours: {e}"

    @mcp.tool()
    async def komoot_get_tour(tour_id: int) -> str:
        """Get full details of a specific Komoot tour by ID.

        Args:
            tour_id: The numeric tour ID
        """
        try:
            tour = await get_client().get_tour(tour_id)
            lines = [f"Tour: {tour.get('name', 'unnamed')}"]
            for key in [
                'id', 'sport', 'status', 'distance', 'elevation_up', 'elevation_down',
                'duration', 'date', 'difficulty_grade', 'difficulty_fitness',
                'difficulty_technical'
            ]:
                val = tour.get(key)
                if val is not None:
                    lines.append(f"  {key}: {val}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error getting tour: {e}"

    @mcp.tool()
    async def komoot_get_user_profile() -> str:
        """Get your Komoot user profile information."""
        try:
            profile = await get_client().get_user_profile()
            if isinstance(profile, dict):
                # ``display_name`` already falls back from username -> email
                # -> user_id in the client, so this never renders the
                # literal "unknown" placeholder kompy sometimes hands back.
                display = profile.get("display_name") or profile.get("email") or "?"
                email = profile.get("email") or "?"
                user_id = profile.get("user_id") or profile.get("username") or "?"
                return f"Profile: {display} ({email}) | User ID: {user_id}"
            return str(profile)
        except Exception as e:
            return f"Error getting profile: {e}"
