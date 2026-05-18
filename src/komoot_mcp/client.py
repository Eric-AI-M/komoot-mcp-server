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
import requests


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
        return {
            "way_type": KomootClient._way_type_name(w),
            "fraction": getattr(w, "amount", None),
        }

    @staticmethod
    def _surface_name(s):
        """Read the readable surface name off a ``kompy.surface.Surface``.

        Same kompy quirk as ``WayType``: the constructor kwarg is
        ``surface_type`` but the attribute is renamed to ``.type``.
        We prefer ``.type`` and fall back to ``.surface_type`` for mocks
        that mirror the kwarg name.
        """
        name = getattr(s, "type", None)
        if name is None:
            name = getattr(s, "surface_type", None)
        return name

    @staticmethod
    def _way_type_name(w):
        """Read the readable way-type name off a ``kompy.way_type.WayType``.

        Mirrors ``_surface_name`` — kompy stores it under ``.type``;
        mocks may use ``.way_type``.
        """
        name = getattr(w, "type", None)
        if name is None:
            name = getattr(w, "way_type", None)
        return name

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
                # Real kompy stores the readable name under ``.type``
                # (constructor kwarg is ``surface_type``). Reading the
                # kwarg name directly is what produced ``? (0)`` rows in
                # production — match the ``_way_type_to_dict`` precedent
                # and prefer ``.type``, fall back for mocks.
                name = self._surface_name(s)
                events.append({
                    "type": "surface",
                    "description": f"{name if name is not None else '?'} "
                                   f"({getattr(s, 'amount', 0)})",
                })
            for w in getattr(summary, "way_types", None) or []:
                name = self._way_type_name(w)
                events.append({
                    "type": "way_type",
                    "description": f"{name if name is not None else '?'} "
                                   f"({getattr(w, 'amount', 0)})",
                })
            return events
        return []

    async def upload_tour(
        self,
        filepath=None,
        data_type=None,
        sport="touringbicycle",
        gpx_content=None,
        tour_name=None,
    ):
        """Upload a GPX/FIT/TCX tour to Komoot.

        Accepts either the GPX content inline (``gpx_content``) or a
        ``filepath`` on the server's filesystem. ``gpx_content`` takes
        precedence — that's the only mode that works under the
        multi-tenant gateway, where the MCP server can't read the
        caller's disk (mirrors the fix shape from issue #9 / PR #12 for
        GPX downloads and route planning).

        For non-GPX uploads (FIT/TCX) the legacy ``filepath`` path is
        still required because those are binary formats.

        Returns a dict ``{"id": <tour_id|None>, "status": "uploaded"}``
        on success. Issue #17: previously this returned the raw kompy
        bool, which the tool layer rendered as
        ``Tour uploaded successfully: False`` on the failure path. We
        now raise ``KomootAPIError`` on a False return so the tool
        layer's except-block surfaces a real error message instead of
        masking failure under "successfully".

        Raises ``KomootAPIError`` if neither ``gpx_content`` nor
        ``filepath`` is supplied, or if ``filepath`` doesn't exist (the
        error message points at ``gpx_content`` for the gateway case),
        or if Komoot rejects the upload.
        """
        import gpxpy

        api = self._get_api()

        # --- gpx_content path (preferred under the gateway) ---
        # gpx_content is GPX XML as a string. We parse it directly —
        # no disk I/O, no temp file. kompy expects a gpxpy.gpx.GPX
        # object for GPX uploads. ``data_type`` defaults to "gpx" in
        # this branch; explicitly passing FIT/TCX with inline content
        # isn't supported because those are binary formats.
        if gpx_content is not None:
            if data_type is None:
                data_type = "gpx"
            if data_type != "gpx":
                raise KomootAPIError(
                    "gpx_content is only supported for GPX uploads. "
                    "For FIT/TCX, use filepath (only works in stdio/"
                    "local-dev mode)."
                )
            tour_obj = gpxpy.parse(gpx_content)
            name = tour_name or self._extract_gpx_name(tour_obj) or "tour"
            raw = await self._call(
                api.upload_tour,
                tour_object=tour_obj,
                activity_type=sport,
                tour_name=name,
            )
            return self._normalize_upload_result(raw)

        # --- filepath path (stdio / local-dev backward compat) ---
        if filepath is None:
            raise KomootAPIError(
                "Either gpx_content (GPX XML as a string) or filepath "
                "(path readable by the MCP server) must be provided. "
                "Under the multi-tenant gateway, the server cannot "
                "read your local filesystem — pass gpx_content."
            )

        if not os.path.exists(filepath):
            raise KomootAPIError(
                f"File not found at {filepath}. If you're calling via "
                f"the gateway, pass gpx_content (the GPX XML as a "
                f"string) instead — the server can't read your local "
                f"filesystem."
            )

        if data_type is None:
            ext = os.path.splitext(filepath)[1].lower().lstrip(".")
            if ext in ("gpx", "fit", "tcx"):
                data_type = ext
            else:
                raise KomootAPIError(
                    f"Cannot determine tour type from extension: {ext}"
                )

        name = tour_name or os.path.splitext(os.path.basename(filepath))[0]

        if data_type == "gpx":
            with open(filepath, "r") as f:
                tour_obj = gpxpy.parse(f)
            raw = await self._call(
                api.upload_tour,
                tour_object=tour_obj,
                activity_type=sport,
                tour_name=name,
            )
            return self._normalize_upload_result(raw)
        else:
            with open(filepath, "rb") as f:
                tour_obj = f.read()
            raw = await self._call(
                api.upload_tour,
                tour_object=tour_obj,
                activity_type=sport,
                tour_name=name,
            )
            return self._normalize_upload_result(raw)

    @staticmethod
    def _normalize_upload_result(raw):
        """Convert kompy's ``upload_tour`` return into a richer dict, or
        raise on failure.

        Issue #17: kompy's ``upload_tour`` returns ``bool`` (True/False)
        and logs the HTTP status code on the server side only. False
        used to bubble up to the tool layer and render as
        ``Tour uploaded successfully: False``. We now:

        * raise ``KomootAPIError`` on False so the tool wrapper's
          ``except`` produces a clear error,
        * pass through dict results (used by tests that capture upload
          kwargs) verbatim,
        * wrap True in a dict with ``status='uploaded'`` and ``id=None``
          (kompy doesn't expose the new tour ID; see issue #19 for the
          follow-up to capture it).
        """
        if raw is True:
            return {"id": None, "status": "uploaded"}
        if raw is False or raw is None:
            raise KomootAPIError(
                "Komoot rejected the upload (HTTP non-2xx — see server "
                "logs for the exact status code). Common cause: the GPX "
                "is in route format (<rte>/<rtept>) rather than track "
                "format (<trk>/<trkseg>/<trkpt>). Use komoot_plan_and_"
                "upload or komoot_plan_route (which now converts) "
                "instead of raw ORS GPX."
            )
        return raw

    @staticmethod
    def _extract_gpx_name(gpx_obj):
        """Best-effort: pull the first track's name from a parsed GPX.

        gpxpy.gpx.GPX exposes a top-level ``name`` and a list of tracks
        each with their own ``name``. We prefer the GPX-level name, fall
        back to the first track. Returns None if neither is present.
        """
        try:
            top = getattr(gpx_obj, "name", None)
            if top:
                return top
            tracks = getattr(gpx_obj, "tracks", None) or []
            for t in tracks:
                tn = getattr(t, "name", None)
                if tn:
                    return tn
        except Exception:
            pass
        return None

    async def upload_gpx_capture_id(
        self,
        gpx_content,
        sport="touringbicycle",
        tour_name=None,
        tour_type="tour_planned",
    ):
        """Upload a GPX directly to Komoot and return the new tour ID.

        kompy's ``upload_tour`` returns only a bool — the Komoot API
        actually responds with ``{"id": <numeric_id>, ...}`` on success
        (HTTP 201) or duplicate (HTTP 202), but kompy throws it away.
        For ``komoot_plan_and_upload`` (issue #19) we need that ID to
        build a tour URL, so we POST to the same endpoint ourselves and
        capture the response body.

        Mirrors kompy's request shape (URL, auth, headers, params) so
        Komoot's server-side behaviour is unchanged, except for one
        extra knob: ``tour_type``. The Komoot ``/v007/tours/`` endpoint
        creates a ``tour_recorded`` activity by default ("I rode this
        today"), which is wrong when the user's intent is "save this
        planned route to Komoot". We pass ``type=tour_planned`` as a
        query param so the uploaded GPX lands under Planned Routes
        instead of Activities. ``tour_recorded`` remains a valid value
        for callers that genuinely uploaded recorded GPS data — they
        should use ``komoot_upload_tour`` instead, which keeps kompy's
        default ``tour_recorded`` behaviour.

        Note: the official Komoot v007 API docs only list ``sport``,
        ``time_in_motion`` and ``name`` for GPX upload; ``type`` is
        undocumented but matches the parameter Komoot's web frontend
        uses when importing a GPX as a planned tour, and matches the
        ``type`` filter on the tour-list GET endpoint.

        Returns ``{"id": <int>, "status": "uploaded"|"duplicate"}`` on
        201/202. Raises ``KomootAPIError`` on any other status code.
        """
        import gpxpy as _gpxpy

        api = self._get_api()
        # Parse + canonicalize GPX in memory. gpxpy round-tripping
        # normalizes whitespace/encoding, which Komoot tolerates fine.
        tour_obj = _gpxpy.parse(gpx_content)
        name = tour_name or self._extract_gpx_name(tour_obj) or "tour"

        # Use the same URL kompy uses. Hard-coded here on purpose: we
        # don't want to import a private constant from kompy that could
        # disappear in a future version.
        url = "https://api.komoot.de/v007/tours/?data_type=gpx"
        params = {
            "sport": sport,
            # Match kompy's default privacy status (FRIENDS) to avoid
            # surprising the user. Privacy override is a future tool
            # parameter (see issue #19).
            "status": "private",
            "data_type": "gpx",
            "name": name,
            "time_in_motion": None,
            # See docstring — drives whether Komoot files this under
            # Planned Routes or Activities.
            "type": tour_type,
        }
        headers = {"User-Agent": "komoot-mcp-server"}
        body = tour_obj.to_xml().encode("utf-8")

        await self.rl.acquire()

        def _post():
            return requests.post(
                url=url,
                auth=(
                    api.authentication.get_email_address(),
                    api.authentication.get_password(),
                ),
                headers=headers,
                params=params,
                data=body,
            )

        try:
            resp = await asyncio.to_thread(_post)
        except Exception as e:
            raise KomootAPIError(f"Komoot upload transport error: {e}")

        if resp.status_code in (201, 202):
            try:
                tour_id = resp.json().get("id")
            except ValueError:
                tour_id = None
            return {
                "id": tour_id,
                "status": "duplicate" if resp.status_code == 202 else "uploaded",
            }

        # Failure path — surface the real status code so the caller
        # knows whether to retry, fix the GPX, or fix credentials.
        snippet = (resp.text or "")[:300]
        raise KomootAPIError(
            f"Komoot rejected the upload (HTTP {resp.status_code}). "
            f"Common 400 cause: GPX is in route format (<rte>/<rtept>) "
            f"rather than track format (<trk>/<trkseg>/<trkpt>). "
            f"Response body (first 300 chars): {snippet}"
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

    # ----- Phase 2: direct REST helpers (bypass kompy) ------------------
    # The four endpoints below are not exposed by kompy. We hit them
    # directly with Basic auth, mirroring the pattern already used by
    # ``upload_gpx_capture_id`` — login via kompy (so we have a valid
    # ``api.authentication`` carrying email + token-as-password), then
    # POST/GET with ``requests`` and the same credentials.
    #
    # Host choice: ``api.komoot.de`` is the documented REST host that
    # accepts Basic auth (kompy itself uses it). The web app's
    # ``www.komoot.com/api/v007/...`` paths are equivalent — same
    # backend gateway — but Basic auth on ``api.komoot.de`` is the
    # known-working path we already rely on for uploads.

    def _basic_auth(self):
        """Return a ``(user_id, token)`` tuple usable as ``requests`` auth.

        kompy's ``Authentication`` stores the long-lived token under
        ``get_password()`` and the numeric user id under
        ``get_username()`` after login. That pair is the Basic-auth
        identity Komoot's REST API accepts (the literal email+password
        pair only works on the v006 ``/account/email/`` login endpoint).
        """
        api = self._get_api()
        return (
            api.authentication.get_username(),
            api.authentication.get_password(),
        )

    async def _http_get_json(self, url, params=None):
        """Authenticated GET that returns the parsed JSON body.

        Goes through the rate limiter and ``asyncio.to_thread`` so the
        event loop is not blocked. Raises ``KomootAPIError`` with a
        useful status code on any non-2xx, mirroring ``_call``'s
        error-message contract for 401/403/404/429 so the existing
        tool-layer ``except Exception`` blocks render the same friendly
        strings.
        """
        auth_pair = self._basic_auth()
        headers = {
            "User-Agent": "komoot-mcp-server",
            # Komoot's API returns HAL+JSON and rejects plain
            # application/json with HTTP 406 HttpMediaTypeNotAcceptable.
            "Accept": "application/hal+json, application/json",
        }

        def _do():
            return requests.get(
                url, auth=auth_pair, headers=headers, params=params, timeout=30,
            )

        await self.rl.acquire()
        try:
            resp = await asyncio.to_thread(_do)
        except Exception as e:
            raise KomootAPIError(f"Komoot transport error: {e}")

        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError as e:
                raise KomootAPIError(f"Komoot returned non-JSON: {e}")
        if resp.status_code in (401, 403):
            raise KomootAPIError(
                "Authentication failed. Check your credentials."
            )
        if resp.status_code == 404:
            raise KomootAPIError("Resource not found. Check the ID.")
        if resp.status_code == 429:
            raise KomootAPIError("Rate limited by Komoot. Try again later.")
        snippet = (resp.text or "")[:200]
        raise KomootAPIError(
            f"Komoot API error: HTTP {resp.status_code} — {snippet}"
        )

    async def get_tour_full(self, tour_id):
        """Single-call full hydrate of a tour.

        Replaces 5+ chained kompy calls (coordinates + way_types +
        surfaces + directions + cover_images + timeline) with one
        embed-everything request. The response is HAL+JSON with all
        embeds populated under ``_embedded``.

        See ``/Users/marcodetering/komoot-endpoint-map.md`` — the URL
        below was live-tested at 200 OK via cookie session and works
        with the same Basic-auth identity kompy already uses.
        """
        url = f"https://api.komoot.de/v007/tours/{int(tour_id)}"
        params = {
            "_embedded": (
                "coordinates,way_types,surfaces,directions,participants,"
                "timeline,cover_images"
            ),
            "directions": "v2",
            "fields": "timeline",
            "format": "coordinate_array",
            "timeline_highlights_fields": "tips,recommenders",
        }
        return await self._http_get_json(url, params=params)

    async def get_highlight(
        self, highlight_id, with_tips=False, with_recommenders=False,
    ):
        """Resolve a Komoot highlight (POI) by ID.

        Returns a dict with keys ``metadata`` (always), ``tips`` (only
        when ``with_tips=True``) and ``recommenders`` (only when
        ``with_recommenders=True``). Tour timeline entries reference
        highlights by ID — without this resolver those IDs are
        dead-ends.

        Endpoints (all live-tested 200 in the endpoint map):

        * ``GET /v007/highlights/{id}``
        * ``GET /v007/highlights/{id}/tips/?page=0``
        * ``GET /v007/highlights/{id}/recommenders/``
        """
        hid = int(highlight_id)
        base = f"https://api.komoot.de/v007/highlights/{hid}"
        out = {"metadata": await self._http_get_json(base)}
        if with_tips:
            try:
                out["tips"] = await self._http_get_json(
                    f"{base}/tips/", params={"page": 0},
                )
            except KomootAPIError as e:
                out["tips_error"] = str(e)
        if with_recommenders:
            try:
                out["recommenders"] = await self._http_get_json(
                    f"{base}/recommenders/",
                )
            except KomootAPIError as e:
                out["recommenders_error"] = str(e)
        return out

    async def get_tour_weather(self, tour_id):
        """Fetch the weather forecast along a tour.

        Hits Komoot's dedicated weather-along-tour service:
        ``GET https://weather-along-tour-api.komoot.de/v1/weather?tour_id={id}``

        The exact query-param shape is best-effort — the endpoint
        wasn't live-probed (the multi-tenant deployment doesn't have
        a shared probing account). We try ``tour_id`` first because
        that's the documented service name; if Komoot rejects with
        400/422 the tool layer surfaces the error verbatim so the
        caller can iterate.
        """
        url = "https://weather-along-tour-api.komoot.de/v1/weather"
        return await self._http_get_json(url, params={"tour_id": int(tour_id)})

    async def discover_near(self, lat, lng, sport=None, limit=10):
        """Discover tours / collections / smart tours near a point.

        Endpoint live-tested at 200:
        ``GET /v007/discover/{lat,lng}/elements/?page=0&_embedded=main_tour,summary``

        Returns the parsed JSON body. Items live under
        ``_embedded.items``. ``sport`` is forwarded as a filter when
        provided. ``limit`` is forwarded as ``limit`` though Komoot may
        clamp it server-side.
        """
        # Komoot's path is ``{lat,lng}`` literal — comma-separated in
        # the URL segment, not a JSON object.
        loc = f"{float(lat)},{float(lng)}"
        url = f"https://api.komoot.de/v007/discover/{loc}/elements/"
        params = {
            "page": 0,
            "limit": int(limit),
            "_embedded": "main_tour,summary",
        }
        if sport:
            params["sport"] = sport
        return await self._http_get_json(url, params=params)
