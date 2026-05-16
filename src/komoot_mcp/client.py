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

    def _call(self, fn, *args, **kwargs):
        self.rl.acquire()
        try:
            return fn(*args, **kwargs)
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

    def get_user_profile(self):
        api = self._get_api()
        # kompy Authentication is companion to KomootConnector
        try:
            auth_obj = kompy.Authentication(
                self.auth.email, self.auth.password
            )
            return {
                "username": auth_obj.get_username(),
                "email": auth_obj.get_email_address(),
            }
        except Exception:
            return {"email": self.auth.email}

    def list_tours(
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

        tours = self._call(api.get_tours, **kwargs)
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

    def get_tour(self, tour_id):
        api = self._get_api()
        tour = self._call(api.get_tour_by_id, str(tour_id))
        if isinstance(tour, kompy.Tour):
            return self._tour_to_dict(tour)
        # If it returned a non-Tour result (GPX/Fit), wrap it
        return {"id": tour_id, "raw": str(type(tour))}

    def get_tour_coordinates(self, tour_id):
        api = self._get_api()
        tour = self._call(api.get_tour_by_id, str(tour_id))
        if isinstance(tour, kompy.Tour):
            coords = tour.generate_coordinates()
            return coords if coords else []
        return []

    def get_tour_gpx(self, tour_id, filepath=None):
        api = self._get_api()
        tour = self._call(api.get_tour_by_id, str(tour_id))
        if isinstance(tour, kompy.Tour):
            gpx_data = tour.generate_gpx_track()
            if gpx_data is None:
                raise KomootAPIError("Failed to generate GPX")
            gpx_str = gpx_data.to_xml() if hasattr(gpx_data, "to_xml") else str(gpx_data)
            if filepath:
                with open(filepath, "w") as f:
                    f.write(gpx_str)
                return filepath
            return gpx_str
        raise KomootAPIError("Could not retrieve tour as GPX")

    def get_tour_fit(self, tour_id, filepath=None):
        api = self._get_api()
        tour = self._call(api.get_tour_by_id, str(tour_id))
        if isinstance(tour, kompy.Tour):
            coords = tour.generate_coordinates()
            if not coords:
                raise KomootAPIError("No coordinates available")
            # FIT export not directly supported by kompy; return coordinates as minimal fallback
            return {"coordinates": coords, "note": "FIT export not supported via kompy"}
        raise KomootAPIError("Could not retrieve tour data")

    def get_tour_directions(self, tour_id):
        api = self._get_api()
        tour = self._call(api.get_tour_by_id, str(tour_id))
        if isinstance(tour, kompy.Tour):
            segments = tour._create_list_segments()
            return segments if segments else []
        return []

    def get_tour_way_types(self, tour_id):
        api = self._get_api()
        tour = self._call(api.get_tour_by_id, str(tour_id))
        if isinstance(tour, kompy.Tour):
            waypoints = tour._create_list_waypoints()
            return waypoints if waypoints else []
        return []

    def get_tour_surfaces(self, tour_id):
        api = self._get_api()
        tour = self._call(api.get_tour_by_id, str(tour_id))
        if isinstance(tour, kompy.Tour):
            info = tour._create_tour_information()
            return info if info else {}
        return {}

    def get_tour_timeline(self, tour_id):
        api = self._get_api()
        tour = self._call(api.get_tour_by_id, str(tour_id))
        if isinstance(tour, kompy.Tour):
            summary = tour._create_tour_summary()
            return summary if summary else {}
        return {}

    def upload_tour(self, filepath, data_type=None):
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
            return self._call(
                api.upload_tour,
                tour_object=tour_obj,
                activity_type="touringbicycle",
                tour_name=tour_name,
            )
        else:
            with open(filepath, "rb") as f:
                tour_obj = f.read()
            tour_name = os.path.splitext(os.path.basename(filepath))[0]
            return self._call(
                api.upload_tour,
                tour_object=tour_obj,
                activity_type="touringbicycle",
                tour_name=tour_name,
            )

    def modify_tour(self, tour_id, name=None, sport=None, status=None):
        api = self._get_api()
        return self._call(
            api.change_tour,
            tour_id=int(tour_id),
            tour_name=name,
            activity_type=sport,
            status=status,
        )

    def delete_tour(self, tour_id):
        api = self._get_api()
        return self._call(api.delete_tour, tour_id=int(tour_id))
