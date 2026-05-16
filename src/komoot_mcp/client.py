import os
import kompy


class KomootAPIError(Exception):
    pass


class KomootClient:
    def __init__(self, auth_manager, rate_limiter):
        self.auth = auth_manager
        self.rl = rate_limiter
        self._api = kompy.Api()

    def _ensure_authenticated(self):
        if not self.auth.is_authenticated():
            self.auth.login()
        self._api.user_id = self.auth.get_user_id()
        self._api.token = self.auth.token

    def _call(self, fn, *args, **kwargs):
        self._ensure_authenticated()
        self.rl.acquire()
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            # kompy wraps requests exceptions; try to extract useful info
            msg = str(e)
            if "401" in msg or "403" in msg:
                raise KomootAPIError("Authentication failed. Check your credentials.")
            if "429" in msg:
                raise KomootAPIError("Rate limited by Komoot. Try again later.")
            if "404" in msg:
                raise KomootAPIError("Resource not found. Check the ID.")
            raise KomootAPIError(f"Komoot API error: {msg}")

    def get_user_profile(self):
        return self._call(self._api.get_profile)

    def list_tours(self, page=0, limit=50, sport_type=None, status=None,
                   start_date=None, end_date=None, name=None,
                   sort_field="date", sort_direction="desc"):
        kwargs = {"page": page, "limit": limit, "sort_field": sort_field, "sort_direction": sort_direction}
        if sport_type:
            kwargs["sport_types"] = sport_type
        if name:
            kwargs["name"] = name
        # kompy handles filtering
        result = self._call(self._api.get_tours, page=page, limit=limit,
                           sport_types=sport_type, status=status,
                           sort_field=sort_field, sort_direction=sort_direction)
        # result is a kompy TourList; extract data
        tours = []
        if hasattr(result, '_embedded') and 'tours' in result._embedded:
            for t in result._embedded['tours']:
                tours.append(self._tour_to_dict(t))
        return {
            "tours": tours,
            "page": getattr(result, 'page', {}),
            "total": len(tours),
        }

    def _tour_to_dict(self, tour):
        """Convert kompy Tour object to plain dict."""
        d = {}
        for key in ['id', 'name', 'sport', 'status', 'distance', 'elevation_up',
                     'elevation_down', 'duration', 'date', 'start_point', 'end_point',
                     'difficulty_grade', 'difficulty_fitness', 'difficulty_technical']:
            val = getattr(tour, key, None)
            if val is not None:
                d[key] = val
        return d

    def get_tour(self, tour_id):
        tour = self._call(self._api.get_tour, tour_id)
        return self._tour_to_dict(tour)

    def get_tour_coordinates(self, tour_id):
        coords = self._call(self._api.fetch_tour_coordinates, tour_id)
        return coords if coords else []

    def get_tour_gpx(self, tour_id, filepath=None):
        gpx_data = self._call(self._api.download_tour, tour_id, "gpx")
        if filepath:
            with open(filepath, 'w') as f:
                f.write(gpx_data)
            return filepath
        return gpx_data

    def get_tour_fit(self, tour_id, filepath=None):
        fit_data = self._call(self._api.download_tour, tour_id, "fit")
        if filepath:
            with open(filepath, 'wb') as f:
                f.write(fit_data)
            return filepath
        return fit_data

    def get_tour_directions(self, tour_id):
        return self._call(self._api.fetch_tour_directions, tour_id)

    def get_tour_way_types(self, tour_id):
        return self._call(self._api.fetch_tour_waytypes, tour_id)

    def get_tour_surfaces(self, tour_id):
        return self._call(self._api.fetch_tour_surfaces, tour_id)

    def get_tour_timeline(self, tour_id):
        return self._call(self._api.fetch_tour_timeline, tour_id)

    def upload_tour(self, filepath, data_type=None):
        if data_type is None:
            ext = os.path.splitext(filepath)[1].lower().lstrip('.')
            if ext in ('gpx', 'fit', 'tcx'):
                data_type = ext
            else:
                raise KomootAPIError(f"Cannot determine tour type from extension: {ext}")
        return self._call(self._api.upload_tour, filepath, data_type)

    def modify_tour(self, tour_id, name=None, sport=None, status=None):
        return self._call(self._api.update_tour, tour_id, name=name, sport=sport, status=status)

    def delete_tour(self, tour_id):
        return self._call(self._api.delete_tour, tour_id)
