"""Write operation tools for Komoot MCP server."""

from komoot_mcp.context import get_client


def register(mcp):
    @mcp.tool()
    async def komoot_upload_tour(
        gpx_content: str = None,
        filepath: str = None,
        data_type: str = None,
        sport: str = "touringbicycle",
        tour_name: str = None,
    ) -> str:
        """Upload a tour to Komoot from GPX content or a file path.

        Prefer ``gpx_content`` — that's the only mode that works under
        the multi-tenant gateway, where the MCP server cannot read the
        caller's local filesystem. ``filepath`` is still supported for
        stdio / local-dev use, and is the only way to upload binary
        FIT/TCX files. Mirrors the inline-content shape introduced for
        ``komoot_get_tour_gpx`` and ``komoot_plan_route`` in PR #12
        (issue #9).

        If both are provided, ``gpx_content`` wins. If neither is
        provided you get a clear error.

        Args:
            gpx_content: GPX XML as a string. Preferred under the
                gateway — pass the body of a .gpx file directly.
            filepath: Path to a GPX/FIT/TCX file on the MCP server's
                filesystem. Only useful in stdio / local-dev mode.
            data_type: File format ('gpx', 'fit', 'tcx'). Auto-detected
                from extension when ``filepath`` is used; defaults to
                'gpx' when ``gpx_content`` is used.
            sport: Komoot activity type (e.g. 'touringbicycle', 'hike',
                'mountainbike', 'racebike', 'jogging'). Defaults to
                'touringbicycle'.
            tour_name: Optional display name for the uploaded tour.
                If omitted, the GPX track name (or the filename, for
                the filepath path) is used.
        """
        try:
            result = await get_client().upload_tour(
                filepath=filepath,
                data_type=data_type,
                sport=sport,
                gpx_content=gpx_content,
                tour_name=tour_name,
            )
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
