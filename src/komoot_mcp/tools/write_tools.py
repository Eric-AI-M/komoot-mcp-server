"""Write operation tools for Komoot MCP server."""

from komoot_mcp.context import get_client


def register(mcp):
    @mcp.tool()
    async def komoot_upload_tour(
        filepath: str,
        data_type: str = None,
        sport: str = "touringbicycle",
    ) -> str:
        """Upload a GPX, FIT, or TCX file as a new Komoot tour.

        Args:
            filepath: Path to the GPX/FIT/TCX file on disk
            data_type: File format ('gpx', 'fit', 'tcx'). Auto-detected from extension if omitted.
            sport: Komoot activity type (e.g. 'touringbicycle', 'hike',
                'mountainbike', 'racebike', 'jogging'). Defaults to
                'touringbicycle'.
        """
        try:
            result = await get_client().upload_tour(filepath, data_type, sport=sport)
            return f"Tour uploaded successfully: {result}"
        except Exception as e:
            return f"Error uploading tour: {e}"

    @mcp.tool()
    async def komoot_modify_tour(
        tour_id: int,
        name: str = None,
        sport: str = None,
        status: str = None,
    ) -> str:
        """Modify a Komoot tour's metadata.

        Args:
            tour_id: The numeric tour ID to modify
            name: New name for the tour
            sport: New sport type (e.g. 'hike', 'touringbicycle', 'mountainbike')
            status: New visibility ('public', 'private', 'friends')
        """
        try:
            result = await get_client().modify_tour(tour_id, name=name, sport=sport, status=status)
            return f"Tour {tour_id} updated: {result}"
        except Exception as e:
            return f"Error modifying tour: {e}"

    @mcp.tool()
    async def komoot_delete_tour(tour_id: int) -> str:
        """Delete a Komoot tour permanently.

        Args:
            tour_id: The numeric tour ID to delete
        """
        try:
            await get_client().delete_tour(tour_id)
            return f"Tour {tour_id} deleted."
        except Exception as e:
            return f"Error deleting tour: {e}"
