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
| `KOMOOT_DATA_DIR` | _Vestigial._ Previously used to stage GPX files on the server's filesystem. The GPX tools now return content inline in the tool response (see [issue #9](https://github.com/Eric-AI-M/komoot-mcp-server/issues/9)); this var is accepted for back-compat but unused. |
| `KOMOOT_RATE_LIMIT` | Outbound requests per second to Komoot. Default `2` |

### Platform-integration mode (Eric AI gateway)

Run the server with `--transport http`. The gateway injects credentials per-request via headers — **no env vars for KOMOOT_EMAIL / KOMOOT_PASSWORD** are needed at the server.

| Header | Purpose |
|---|---|
| `X-User-Credentials` | JSON object `{"email": "user@example.com", "password": "...", "ors_api_key": "..."}`. Parsed per request, used to build a request-scoped `AuthManager` and (optionally) a per-tenant `RoutingManager`, and discarded when the response is sent. `ors_api_key` is optional — only required if the user wants to call `komoot_plan_route`. |
| `X-Internal-Secret` | When the `INTERNAL_SECRET` env var is set on the server, every non-`/health` request must include a matching value or be rejected with 401. |

Each request gets its own `AuthManager` + `KomootClient` via `contextvars.ContextVar`, so concurrent users never see each other's credentials. The OpenRouteService API key for `komoot_plan_route` flows through the same `X-User-Credentials` JSON payload as a per-org credential — no process-wide ORS key is needed in HTTP mode.

If both an env var and an `X-User-Credentials` header are present, the header wins. For `ORS_API_KEY` specifically, the env var is only consulted as a fallback when no `ors_api_key` was forwarded for the request (mainly stdio/local-dev).

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
| `komoot_get_tour_gpx` | Return a tour's GPX content inline in the tool response as a fenced `xml` code block. Oversized bodies are truncated; the full byte count is always reported. |
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
| `komoot_plan_route` | Plan a route using OpenRouteService with sport profiles, trail/road preferences, roundtrip support, and optional waypoints. Returns the GPX content inline in the response so the caller can save or forward it directly. |

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
