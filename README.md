# Komoot MCP Server

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io) server that gives AI assistants like Claude access to Komoot. Browse, search, and download tours; upload new activities; plan routes with AI assistance using OpenRouteService; and geocode locations — all through natural language.

The server runs in two modes:

* **Local stdio** for single-user use with credentials in env vars.
* **Streamable HTTP** behind the Eric AI platform gateway, where the gateway forwards per-user credentials on every request so one server process can safely serve many tenants.

## Features

- **Browse & search** your Komoot tours with rich filtering (sport type, visibility, sort)
- **Download tours** as GPX files
- **Upload activities** to Komoot (GPX, FIT, TCX) with a configurable sport type
- **AI-assisted route planning** via OpenRouteService with sport-specific profiles, trail/road preferences, roundtrip support, and waypoints
- **Geocoding** via Komoot's Photon API (free, no API key needed)
- **Manage tours** — modify metadata and delete tours

## Installation

```bash
pip install komoot-mcp-server
```

Or install from source:

```bash
git clone https://github.com/marcodetering-prog/komoot-mcp-server.git
cd komoot-mcp-server
pip install -e .
```

## Configuration

### Local / stdio mode

In stdio mode, credentials come from environment variables.

| Variable | Description |
|---|---|
| `KOMOOT_EMAIL` | Your Komoot account email address |
| `KOMOOT_PASSWORD` | Your Komoot account password |
| `ORS_API_KEY` | Optional [OpenRouteService API key](https://openrouteservice.org) — enables the `komoot_plan_route` tool |
| `KOMOOT_DATA_DIR` | Directory for GPX downloads when no explicit `filepath` is given. Defaults to `/tmp/komoot` |
| `KOMOOT_RATE_LIMIT` | Outbound requests per second to Komoot. Default `2` |

### Platform-integration mode (Eric AI gateway)

Run the server with `--transport http`. The gateway injects credentials per-request via headers — **no env vars for KOMOOT_EMAIL / KOMOOT_PASSWORD** are needed at the server.

| Header | Purpose |
|---|---|
| `X-User-Credentials` | JSON object `{"email": "user@example.com", "password": "..."}`. Parsed per request, used to build a request-scoped `AuthManager`, and discarded when the response is sent. |
| `X-Internal-Secret` | When the `INTERNAL_SECRET` env var is set on the server, every non-`/health` request must include a matching value or be rejected with 401. |

Each request gets its own `AuthManager` + `KomootClient` via `contextvars.ContextVar`, so concurrent users never see each other's credentials.

If both an env var and an `X-User-Credentials` header are present, the header wins.

## Usage with Claude

Add this to your Claude configuration (`claude_desktop_config.json` or `.claude/mcp.json`):

```json
{
  "mcpServers": {
    "komoot": {
      "command": "python",
      "args": ["-m", "komoot_mcp.server"],
      "env": {
        "KOMOOT_EMAIL": "your-email@example.com",
        "KOMOOT_PASSWORD": "your-password",
        "ORS_API_KEY": "your-ors-api-key"
      }
    }
  }
}
```

Once connected, you can ask Claude things like:

- "Show me my recent hiking tours"
- "Download tour 12345 as a GPX file"
- "Plan a 10km roundtrip trail run starting from Berlin"
- "Upload the GPX file to Komoot"
- "Geocode 'Marienplatz Munich'"
- "What way types and surfaces does tour 12345 have?"

## Available Tools (15)

### Authentication

| Tool | Description |
|---|---|
| `komoot_login` | Authenticate with Komoot. In platform mode the gateway provides creds via header; in stdio mode env vars are used. |

### Browse & Search

| Tool | Description |
|---|---|
| `komoot_list_tours` | List your tours with filters for sport type, visibility, name search, sorting, and pagination |
| `komoot_get_tour` | Get full details of a specific tour (distance, elevation, duration, difficulty) |
| `komoot_get_user_profile` | Retrieve your Komoot profile information |

### Tour Data

| Tool | Description |
|---|---|
| `komoot_get_tour_coordinates` | Get the coordinate array (lat, lng, altitude) for a tour |
| `komoot_get_tour_gpx` | Download a tour as a GPX file. Saves to `$KOMOOT_DATA_DIR/tour_<id>_*.gpx` when no path is given. |
| `komoot_get_tour_directions` | Get turn-by-turn directions for a tour |
| `komoot_get_tour_way_types` | Get the way type breakdown (road, trail, path percentages) |
| `komoot_get_tour_surfaces` | Get the surface breakdown (paved, gravel, trail percentages) |
| `komoot_get_tour_timeline` | Get the event timeline for a tour |

> FIT export (`komoot_get_tour_fit`) was removed — kompy does not support generating FIT files. Use the GPX export instead.

### Write Operations

| Tool | Description |
|---|---|
| `komoot_upload_tour` | Upload a GPX, FIT, or TCX file as a new Komoot tour. Pass `sport=` to choose the activity type (default `touringbicycle`). |
| `komoot_modify_tour` | Modify a tour's metadata (name, sport type, visibility) |
| `komoot_delete_tour` | Permanently delete a tour |

### Routing & Geocoding

| Tool | Description |
|---|---|
| `komoot_geocode` | Geocode a place name or reverse-geocode coordinates using Komoot's Photon API |
| `komoot_plan_route` | Plan a route using OpenRouteService with sport profiles, trail/road preferences, roundtrip support, and optional waypoints. Saves the result as GPX in `$KOMOOT_DATA_DIR`, ready for upload. |

#### Sport Profiles

The `komoot_plan_route` tool supports these sport profiles, each mapped to an OpenRouteService routing profile:

| Sport | ORS Profile |
|---|---|
| `hike` | foot-hiking |
| `trail_run` | foot-walking |
| `mountain_bike` | cycling-mountain |
| `road_cycle` | cycling-road |
| `gravel_ride` | cycling-regular |

## Dependencies

- Python >= 3.11
- [mcp](https://pypi.org/project/mcp/) — MCP framework
- [kompy](https://pypi.org/project/kompy/) — Komoot API client (pinned `<0.1.0`; we depend on a few `Tour._create_*` internals)
- [gpxpy](https://pypi.org/project/gpxpy/) — GPX parsing for uploads
- [openrouteservice](https://pypi.org/project/openrouteservice/) — OpenRouteService client (optional, for route planning)

## License

This project is provided as-is. See the repository for license details.

## Links

- [GitHub Repository](https://github.com/marcodetering-prog/komoot-mcp-server)
- [MCP Protocol Documentation](https://modelcontextprotocol.io)
- [OpenRouteService](https://openrouteservice.org)
