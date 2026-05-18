"""Thin async wrapper around the ``kompy`` Komoot SDK.

NOTE — RELIANCE ON KOMPY ATTRIBUTES:
``Tour.__init__`` eagerly populates ``tour.summary`` (TourSummary),
``tour.tour_information`` (List[TourInformation]), ``tour.segments``
(List[Segment]) and ``tour.path`` (List[Waypoint]) from the underlying
API response when the corresponding keys are present. We read those
populated attributes directly rather than re-calling the private
``Tour._create_*`` static methods — those are staticmethods that take
the *raw dict slices* (e.g. ``tour['summary']``), which we don't have
once kompy has constructed the Tour object. Calling them as instance
methods (the previous shape) raised
``missing 1 required positional argument: 'tour_summary'`` /
``'tour_information_array'``. We still pin ``kompy<0.1.0`` in
``pyproject.toml`` for stability.

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
        # KomootConnector.__init__ already performs the login HTTP call
        # and populates ``self.authentication`` with the token + username
        # (see kompy.komoot_connector). The previous code constructed a
        # second ``kompy.Authentication`` directly, which has no ``.login``
        # method, so calling ``.get_username()`` raised "No username set,
        # please login first." The earlier silent ``except`` masked that
        # bug; with the swallow removed we now correctly read the username
        # off the already-authenticated connector instead.
        #
        # NOTE on the "username" field: kompy's login response stores the
        # numeric Komoot user ID under the ``username`` key, and there is
        # no public-API display name field. ``get_username`` therefore
        # returns the user ID as a string (or the placeholder "unknown"
        # if Komoot ever omits it). We expose a derived ``display_name``
        # that falls back to email, then user_id, so callers always have
        # something useful to render.
        api = self._get_api()
        username = await asyncio.to_thread(api.authentication.get_username)
        email = await asyncio.to_thread(api.authentication.get_email_address)
        # In kompy, get_username() is the user ID. Keep both names for
        # clarity and back-compat with any caller still reading "username".
        user_id = username
        if not username or username == "unknown":
            display_name = email or (str(user_id) if user_id else "unknown")
        else:
            display_name = username
        return {
            "display_name": display_name,
            "username": username,
            "user_id": user_id,
            "email": email,
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
            # kompy's Tour.generate_coordinates requires the Authentication
            # object — the connector populates ``api.authentication`` post-
            # login. Passing it via positional arg matches the kompy
            # signature exactly. The call mutates ``tour.coordinates`` and
            # returns a bool; we return the populated list (or []).
            ok = await asyncio.to_thread(
                tour.generate_coordinates, api.authentication,
            )
            if not ok:
                return []
            return tour.coordinates or []
        return []

    async def get_tour_gpx(self, tour_id):
        """Return the GPX XML for a tour as an in-memory string.

        kompy's ``Tour.generate_gpx_track(authentication)`` populates
        ``tour.gpx_track`` in memory (the call returns True/False). We
        return that string directly — see issue #9: writing the GPX to a
        path on the MCP server's filesystem (formerly ``KOMOOT_DATA_DIR``)
        was useless in the multi-tenant gateway deployment, because the
        caller has no access to the server's disk. ``KOMOOT_DATA_DIR``
        is now vestigial.
        """
        api = self._get_api()
        tour = await self._call(api.get_tour_by_id, str(tour_id))
        if isinstance(tour, kompy.Tour):
            await asyncio.to_thread(
                tour.generate_gpx_track, api.authentication,
            )
            gpx_data = getattr(tour, "gpx_track", None)
            if gpx_data is None:
                raise KomootAPIError("Failed to generate GPX")
            gpx_str = gpx_data.to_xml() if hasattr(gpx_data, "to_xml") else str(gpx_data)
            return gpx_str
        raise KomootAPIError("Could not retrieve tour as GPX")

    async def get_tour_directions(self, tour_id):
        api = self._get_api()
        tour = await self._call(api.get_tour_by_id, str(tour_id))
        if isinstance(tour, kompy.Tour):
            # ``Tour.__init__`` already populates ``self.segments`` via
            # ``_create_list_segments`` when the API response has a
            # 'segments' key — read it directly. Calling the static
            # ``_create_list_segments`` as a bound method previously
            # raised ``missing 1 required positional argument``.
            segments = getattr(tour, "segments", None) or []
            return [self._segment_to_dict(s) for s in segments]
        return []

    @staticmethod
    def _segment_to_dict(segment):
        boundaries = getattr(segment, "segment_boundaries", None)
        return {
            "type": getattr(segment, "segment_type", None),
            "reference": getattr(segment, "reference", None),
            "from": getattr(boundaries, "start_index_point", None) if boundaries else None,
            "to": getattr(boundaries, "end_index_point", None) if boundaries else None,
        }

    async def get_tour_way_types(self, tour_id):
        api = self._get_api()
        tour = await self._call(api.get_tour_by_id, str(tour_id))
        if isinstance(tour, kompy.Tour):
            # Way-type breakdown lives on ``tour.summary.way_types``
            # (List[kompy.way_type.WayType]) — each carries ``.type`` and
            # ``.amount``. The previous code returned ``tour.path`` (a
            # list of ``Waypoint`` objects with no ``__repr__``), so the
            # MCP tool surfaced raw ``<...Waypoint object at 0x...>``
            # strings. Mirror ``get_tour_surfaces``'s serializer shape:
            # emit plain dicts the tool layer can render.
            #
            # NOTE: kompy's WayType constructor takes ``way_type=...`` but
            # stores it as ``self.type``. We accept both attribute names
            # to stay compatible with the MagicMock-based tests, which
            # set ``way_type=...`` directly on the mock.
            summary = getattr(tour, "summary", None)
            if summary is None:
                return []
            return [self._way_type_to_dict(w)
                    for w in (getattr(summary, "way_types", None) or [])]
        return []

    @staticmethod
    def _way_type_to_dict(w):
        """Serialize a ``kompy.way_type.WayType`` to ``{way_type, fraction}``.

        kompy stores the readable type string under ``.type`` (its
        constructor kwarg is ``way_type`` but the attribute is renamed).
        We fall back to ``.way_type`` for test mocks that mirror the
        kwarg name.
        """
        name = getattr(w, "type", None)
        if name is None:
            name = getattr(w, "way_type", None)
        return {
            "way_type": name,
            "fraction": getattr(w, "amount", None),
        }

    async def get_tour_surfaces(self, tour_id):
        api = self._get_api()
        tour = await self._call(api.get_tour_by_id, str(tour_id))
        if isinstance(tour, kompy.Tour):
            # ``Tour.__init__`` populates ``self.tour_information``
            # (List[TourInformation]) via ``_create_tour_information``
            # when the API response has 'tour_information' — read it
            # directly. Returning a list (was {}) since the underlying
            # attribute is a list of TourInformation objects.
            tour_info = getattr(tour, "tour_information", None) or []
            return [
                {
                    "type": getattr(ti, "tour_information_type", None),
                    "segments": [
                        {
                            "from": getattr(s, "start_index_point", None),
                            "to": getattr(s, "end_index_point", None),
                        }
                        for s in (getattr(ti, "segments", None) or [])
                    ],
                }
                for ti in tour_info
            ]
        return []

    async def get_tour_timeline(self, tour_id):
        api = self._get_api()
        tour = await self._call(api.get_tour_by_id, str(tour_id))
        if isinstance(tour, kompy.Tour):
            # ``Tour.__init__`` populates ``self.summary`` (TourSummary)
            # via ``_create_tour_summary`` when the API response has a
            # 'summary' key — read it directly. Previously we called the
            # static ``_create_tour_summary`` with no args, which raised
            # ``missing 1 required positional argument: 'tour_summary'``.
            #
            # The kompy ``TourSummary`` carries two lists (surfaces +
            # way_types). Flatten them into a single list of dicts the
            # existing data_tools renderer can iterate over — preserves
            # the public tool signature (still returns a list-like).
            summary = getattr(tour, "summary", None)
            if summary is None:
                return []
            events = []
            for s in getattr(summary, "surfaces", None) or []:
                events.append({
                    "type": "surface",
                    "description": f"{getattr(s, 'surface_type', '?')} "
                                   f"({getattr(s, 'amount', 0)})",
                })
            for w in getattr(summary, "way_types", None) or []:
                events.append({
                    "type": "way_type",
                    "description": f"{getattr(w, 'way_type', '?')} "
                                   f"({getattr(w, 'amount', 0)})",
                })
            return events
        return []

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
