"""Thin async wrapper around the ``kompy`` Komoot SDK.

NOTE — RELIANCE ON KOMPY INTERNALS:
The ``get_tour_directions``, ``get_tour_way_types``, ``get_tour_surfaces``
and ``get_tour_timeline`` helpers below call private ``Tour._create_*``
methods. These are not part of the kompy public API and may break in
future versions, so we pin ``kompy<0.1.0`` in ``pyproject.toml``.

NOTE — ASYNC SHAPE:
All API methods are coroutines. They wrap synchronous kompy calls in
``asyncio.to_thread`` so the event loop is never blocked by blocking
HTTP. Rate limiting is awaited prior to each call.
"""
from __future__ import annotations

import asyncio
import os

import kompy


class KomootAPIError(Exception):
    pass


class KomootClient:
    def __init__(self, auth_manager, rate_limiter):
        self.auth = auth_manager
        self.rl = rate_limiter
        self._api = None

    def _get_api(self):
        """Lazily create the kompy connector with stored credentials."""
        if self._api is None:
            email = self.auth.email
            password = self.auth.password
            if not email or not password:
                raise KomootAPIError(
                    "KOMOOT_EMAIL and KOMOOT_PASSWORD must be set"
                )
            self._api = kompy.KomootConnector(email, password)
        return self._api

    async def _call(self, fn, *args, **kwargs):
        await self.rl.acquire()
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except Exception as e:
            msg = str(e)
            if "401" in msg or "403" in msg:
                raise KomootAPIError(
                    "Authentication failed. Check your credentials."
                )
            if "429" in msg:
                raise KomootAPIError(
                    "Rate limited by Komoot. Try again later."
                )
            if "404" in msg:
                raise KomootAPIError(
                    "Resource not found. Check the ID."
                )
            raise KomootAPIError(f"Komoot API error: {msg}")

    async def get_user_profile(self):
        # Surfaces kompy errors so handlers can surface them; the
        # previous silent fallback masked auth/network failures and
        # made debugging painful.
        self._get_api()  # validates credentials are present
        auth_obj = await asyncio.to_thread(
            kompy.Authentication, self.auth.email, self.auth.password
        )
        return {
            "username": await asyncio.to_thread(auth_obj.get_username),
            "email": await asyncio.to_thread(auth_obj.get_email_address),
        }

    async def list_tours(
        self,
        page=0,
        limit=50,
        sport_type=None,
        status=None,
        start_date=None,
        end_date=None,
        name=None,
        sort_field="date",
        sort_direction="desc",
    ):
        api = self._get_api()
        kwargs = {
            "limit": limit,
            "page": page,
            "sort_field": sort_field,
        }
        if sport_type:
            kwargs["sport_types"] = sport_type
        if status:
            kwargs["status"] = status
        if name:
            kwargs["tour_name"] = name
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date

        tours = await self._call(api.get_tours, **kwargs)
        return {
            "tours": [self._tour_to_dict(t) for t in tours],
            "page": {"page": page, "limit": limit},
            "total": len(tours),
        }

    def _tour_to_dict(self, tour):
        """Convert kompy Tour object to plain dict."""
        d = {}
        # Tour stores data in internal _create_* methods;
        # common attributes accessible directly
        for key in [
            "id", "name", "sport", "status", "distance",
            "elevation_up", "elevation_down", "duration", "date",
            "start_point", "end_point", "difficulty_grade",
            "difficulty_fitness", "difficulty_technical",
        ]:
            val = getattr(tour, key, None)
            if val is not None:
                d[key] = val
        return d

    async def get_tour(self, tour_id):
        api = self._get_api()
        tour = await self._call(api.get_tour_by_id, str(tour_id))
        if isinstance(tour, kompy.Tour):
            return self._tour_to_dict(tour)
        # If it returned a non-Tour result (GPX/Fit), wrap it
        return {"id": tour_id, "raw": str(type(tour))}

    async def get_tour_coordinates(self, tour_id):
        api = self._get_api()
        tour = await self._call(api.get_tour_by_id, str(tour_id))
        if isinstance(tour, kompy.Tour):
            coords = await asyncio.to_thread(tour.generate_coordinates)
            return coords if coords else []
        return []

    async def get_tour_gpx(self, tour_id, filepath=None):
        api = self._get_api()
        tour = await self._call(api.get_tour_by_id, str(tour_id))
        if isinstance(tour, kompy.Tour):
            gpx_data = await asyncio.to_thread(tour.generate_gpx_track)
            if gpx_data is None:
                raise KomootAPIError("Failed to generate GPX")
            gpx_str = gpx_data.to_xml() if hasattr(gpx_data, "to_xml") else str(gpx_data)
            if filepath:
                # Filesystem I/O is small; keep it inline for simplicity.
                with open(filepath, "w") as f:
                    f.write(gpx_str)
                return filepath
            return gpx_str
        raise KomootAPIError("Could not retrieve tour as GPX")

    async def get_tour_directions(self, tour_id):
        api = self._get_api()
        tour = await self._call(api.get_tour_by_id, str(tour_id))
        if isinstance(tour, kompy.Tour):
            # Relies on kompy internal — see module docstring.
            segments = await asyncio.to_thread(tour._create_list_segments)
            return segments if segments else []
        return []

    async def get_tour_way_types(self, tour_id):
        api = self._get_api()
        tour = await self._call(api.get_tour_by_id, str(tour_id))
        if isinstance(tour, kompy.Tour):
            # Relies on kompy internal — see module docstring.
            waypoints = await asyncio.to_thread(tour._create_list_waypoints)
            return waypoints if waypoints else []
        return []

    async def get_tour_surfaces(self, tour_id):
        api = self._get_api()
        tour = await self._call(api.get_tour_by_id, str(tour_id))
        if isinstance(tour, kompy.Tour):
            # Relies on kompy internal — see module docstring.
            info = await asyncio.to_thread(tour._create_tour_information)
            return info if info else {}
        return {}

    async def get_tour_timeline(self, tour_id):
        api = self._get_api()
        tour = await self._call(api.get_tour_by_id, str(tour_id))
        if isinstance(tour, kompy.Tour):
            # Relies on kompy internal — see module docstring.
            summary = await asyncio.to_thread(tour._create_tour_summary)
            return summary if summary else {}
        return {}

    async def upload_tour(self, filepath, data_type=None, sport="touringbicycle"):
        import gpxpy

        api = self._get_api()
        if data_type is None:
            ext = os.path.splitext(filepath)[1].lower().lstrip(".")
            if ext in ("gpx", "fit", "tcx"):
                data_type = ext
            else:
                raise KomootAPIError(
                    f"Cannot determine tour type from extension: {ext}"
                )

        if data_type == "gpx":
            with open(filepath, "r") as f:
                tour_obj = gpxpy.parse(f)
            # Extract name from filename
            tour_name = os.path.splitext(os.path.basename(filepath))[0]
            return await self._call(
                api.upload_tour,
                tour_object=tour_obj,
                activity_type=sport,
                tour_name=tour_name,
            )
        else:
            with open(filepath, "rb") as f:
                tour_obj = f.read()
            tour_name = os.path.splitext(os.path.basename(filepath))[0]
            return await self._call(
                api.upload_tour,
                tour_object=tour_obj,
                activity_type=sport,
                tour_name=tour_name,
            )

    async def modify_tour(self, tour_id, name=None, sport=None, status=None):
        api = self._get_api()
        return await self._call(
            api.change_tour,
            tour_id=int(tour_id),
            tour_name=name,
            activity_type=sport,
            status=status,
        )

    async def delete_tour(self, tour_id):
        api = self._get_api()
        return await self._call(api.delete_tour, tour_id=int(tour_id))
