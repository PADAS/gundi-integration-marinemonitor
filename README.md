# gundi-integration-marinemonitor

Gundi v2 connector for [Marine Monitor](https://www.protectedseas.net/marine-monitor/) — a coastal radar system that tracks vessel movements. This connector pulls vessel track data from the Marine Monitor API and forwards it to EarthRanger via Gundi.

---

## What it does

Every 5 minutes the connector:

1. **Fetches radar station data** from the Marine Monitor API, including all active vessel tracks per station
2. **Filters tracks** below a configurable confidence threshold
3. **Sends observations directly to EarthRanger** via the generic sensor handler endpoint (`/api/v1.0/sensors/generic/{provider_key}/status`) — one observation per track, formatted as `tracking-device` / `vehicle` subject type
4. **Assigns vessels to a subject group** in EarthRanger (optional) — subjects are assigned to the configured group at creation time
5. **Deactivates stale subjects** in EarthRanger — vessels that have disappeared from the API are marked as inactive

---

## Configuration

When setting up an integration in the Gundi portal, the following fields are required:

| Field | Required | Description |
|---|---|---|
| `api_url` | Yes | Full Marine Monitor API URL including account ID. Example: `https://m2mobile.protectedseas.net/api/map/42/earthranger` |
| `api_key` | Yes | API key for the Marine Monitor API (sent as Authorization header) |
| `earthranger_subject_group_name` | No | Name of the EarthRanger subject group to assign vessel subjects to. The group will be created automatically if it does not exist. If omitted, subjects are added to ER's default group |
| `minimal_confidence` | No (default `0.1`) | Confidence threshold (0.0–1.0). Tracks below this value are ignored |

**EarthRanger credentials are not configured here.** The connector reads the ER URL and token automatically from the destination integration record in Gundi.

---

## Observations sent to EarthRanger

Each vessel track is posted directly to EarthRanger's generic sensor handler. The source provider key is set to `gundi_marinemonitor_<integration_id>`. The payload looks like:

```json
{
  "manufacturer_id": "vessel-<track_id>",
  "recorded_at": "<ISO timestamp>",
  "location": {
    "lat": 25.811533,
    "lon": -111.306303
  },
  "subject_groups": ["Marine Monitor"],
  "additional": {
    "speed_kmph": 5.5,
    "heading": 356.0,
    "bearing": 330.02,
    "distance_nm": 17.02,
    "confidence": 1.0,
    "radar_track_id": 538071772,
    "vessel_name": "My Vessel",
    "track_source": "ais",
    "active": true,
    "radar_station_name": "Loreto 2",
    "radar_station_id": 42
  }
}
```

`subject_groups` is only included when `earthranger_subject_group_name` is configured.

---

## Expected behavior in EarthRanger

- Each vessel track creates a **source** (identified by `manufacturer_id` = `vessel-<track_id>`) and a linked **subject** of type `vehicle` / subtype `vessel`, under the source provider `gundi_marinemonitor_<integration_id>`
- If `earthranger_subject_group_name` is configured, the subject is assigned to that group at creation time. The group is created in ER automatically if it doesn't exist
- When a vessel stops appearing in the API, its subject is set to `is_active: false`
- The subject with the most recent `last_position_date` is used for deactivation

---

## State management

The connector uses Gundi's state manager (Redis-backed) to track vessels across runs:

- `track_index` — stores the full list of known track IDs and the last run timestamp
- Per-track state — stores `last_seen` timestamp used for stale subject detection

---

## Local development setup

### 1. Set up environment variables

```bash
cp local/.env.local.example local/.env.local
```

Edit `local/.env.local` and set at minimum:
- `KEYCLOAK_CLIENT_SECRET` — get this from the Gundi team
- `INTEGRATION_SERVICE_URL` — the public URL where this action runner is deployed

Make sure all values are unquoted (Docker's `--env-file` does not strip quotes):
```
# correct
LOG_LEVEL=DEBUG

# wrong — will cause errors
LOG_LEVEL="DEBUG"
```

---

## Registering the integration with Gundi

Before the connector can be used, it must be registered with the Gundi platform. This creates the integration type and action definitions in the portal.

### Prerequisites

Make sure `local/.env.local` has these two values set correctly before registering:

```
INTEGRATION_TYPE_SLUG=marine_monitor
INTEGRATION_SERVICE_URL=https://marinemonitor-actions-runner-426960321326.us-central1.run.app
```

**Common mistakes that will cause the registration to fail:**
- The slug only allows lowercase letters, numbers, and underscores — **no hyphens** (e.g. `marine-monitor` will fail, use `marine_monitor`)
- `INTEGRATION_SERVICE_URL` must be a valid URL — leaving it as a placeholder like `https://your-action-runner-url` will fail
- All values in `.env.local` must be **unquoted** — Docker's `--env-file` does not strip quotes and will pass them as part of the value
- If the integration type already exists in the portal with the same slug, registration will return `400`. Ask the Gundi team to delete it first, or use a different slug.

### Running registration

Use the **"Register integration (Docker)"** VS Code task (`Cmd+Shift+P` → Tasks: Run Task). It builds the Docker image and runs the register script inside the container.

> **Do not try to run the register script directly with your local Python.** The codebase requires Python 3.10 and specific package versions that conflict with macOS system Python and Homebrew-managed environments. Docker is the only reliable way to run it locally.

You can also set `REGISTER_ON_START=True` in `local/.env.local` to register automatically every time the container starts.

---

## Running locally (Docker)

```bash
cd local
docker compose up --build
```

The API will be available at `http://localhost:8080/docs`.

---

## Running tests

```bash
docker build -f docker/Dockerfile --target devimage -t mm-test .
docker run --rm -v $(pwd)/app:/code/app mm-test pytest app/actions/tests/ -v
```
