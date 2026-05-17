"""Tour data tools for Komoot MCP server."""

import os
import tempfile

from komoot_mcp.context import get_client


def _default_gpx_path(tour_id: int) -> str:
    """Pick a writable GPX target inside ``KOMOOT_DATA_DIR``.

    Hard-coded ``./tour_{id}.gpx`` failed under the container (CWD
    ``/app`` is owned by root and the process runs as ``nobody``).
    Use a tempfile path under the configured data dir instead.
    """
    data_dir = os.environ.get("KOMOOT_DATA_DIR", "/tmp/komoot")
    os.makedirs(data_dir, exist_ok=True)
    fd = tempfile.NamedTemporaryFile(
        delete=False,
        prefix=f"tour_{tour_id}_",
        suffix=".gpx",
        dir=data_dir,
    )
    path = fd.name
    fd.close()
    return path


def register(mcp):
    @mcp.tool()
    async def komoot_get_tour_coordinates(tour_id: int) -> str:
        """Get the coordinate array (lat, lng, altitude) for a tour.

        Args:
            tour_id: The numeric tour ID
        """
        try:
            coords = await get_client().get_tour_coordinates(tour_id)
            if not coords:
                return "No coordinates found."
            lines = [f"Tour {tour_id}: {len(coords)} coordinate points"]
            for i, c in enumerate(coords[:5]):
                if isinstance(c, dict):
                    lines.append(f"  [{i}] lat={c.get('lat')}, lng={c.get('lng')}, alt={c.get('alt', '?')}")
                elif isinstance(c, (list, tuple)) and len(c) >= 2:
                    alt = c[2] if len(c) >= 3 else '?'
                    lines.append(f"  [{i}] lat={c[0]}, lng={c[1]}, alt={alt}")
            if len(coords) > 5:
                lines.append(f"  ... and {len(coords) - 5} more points")
            return "\n".join(lines)
        except Exception as e:
            return f"Error getting coordinates: {e}"

    @mcp.tool()
    async def komoot_get_tour_gpx(tour_id: int, filepath: str = None) -> str:
        """Download a tour as a GPX file.

        Args:
            tour_id: The numeric tour ID
            filepath: Path to save the GPX file. If not provided, saves to
                a tempfile in ``KOMOOT_DATA_DIR`` (default ``/tmp/komoot``).
        """
        try:
            if filepath is None:
                filepath = _default_gpx_path(tour_id)
            result = await get_client().get_tour_gpx(tour_id, filepath)
            return f"GPX saved to: {result}"
        except Exception as e:
            return f"Error downloading GPX: {e}"

    @mcp.tool()
    async def komoot_get_tour_directions(tour_id: int) -> str:
        """Get turn-by-turn directions for a tour."""
        try:
            directions = await get_client().get_tour_directions(tour_id)
            if not directions:
                return "No directions found."
            lines = [f"Tour {tour_id} directions:"]
            for d in directions[:20]:
                if isinstance(d, dict):
                    lines.append(f"  {d.get('text', str(d))}")
                else:
                    lines.append(f"  {d}")
            if len(directions) > 20:
                lines.append(f"  ... and {len(directions) - 20} more steps")
            return "\n".join(lines)
        except Exception as e:
            return f"Error getting directions: {e}"

    @mcp.tool()
    async def komoot_get_tour_way_types(tour_id: int) -> str:
        """Get the way type breakdown for a tour (road, trail, path percentages)."""
        try:
            way_types = await get_client().get_tour_way_types(tour_id)
            if not way_types:
                return "No way type data found."
            if isinstance(way_types, list):
                return f"Way types for tour {tour_id}:\n" + "\n".join(f"  {w}" for w in way_types)
            return f"Way types for tour {tour_id}: {way_types}"
        except Exception as e:
            return f"Error getting way types: {e}"

    @mcp.tool()
    async def komoot_get_tour_surfaces(tour_id: int) -> str:
        """Get the surface breakdown for a tour (paved, gravel, trail percentages)."""
        try:
            surfaces = await get_client().get_tour_surfaces(tour_id)
            if not surfaces:
                return "No surface data found."
            if isinstance(surfaces, list):
                return f"Surfaces for tour {tour_id}:\n" + "\n".join(f"  {s}" for s in surfaces)
            return f"Surfaces for tour {tour_id}: {surfaces}"
        except Exception as e:
            return f"Error getting surfaces: {e}"

    @mcp.tool()
    async def komoot_get_tour_timeline(tour_id: int) -> str:
        """Get the event timeline for a tour."""
        try:
            timeline = await get_client().get_tour_timeline(tour_id)
            if not timeline:
                return "No timeline events found."
            lines = [f"Tour {tour_id} timeline:"]
            for event in timeline[:20]:
                if isinstance(event, dict):
                    lines.append(f"  {event.get('type', 'event')}: {event.get('description', str(event))}")
                else:
                    lines.append(f"  {event}")
            if len(timeline) > 20:
                lines.append(f"  ... and {len(timeline) - 20} more events")
            return "\n".join(lines)
        except Exception as e:
            return f"Error getting timeline: {e}"
