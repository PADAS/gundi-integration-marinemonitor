# AGENTS.md
---
description:
globs:
alwaysApply: true
---

## Project Overview
`gundi-integration-marinemonitor` is a Gundi connector that pulls vessel tracking data from the **Marine Monitor radar API** every 5 minutes and forwards it to EarthRanger. It handles the full vessel lifecycle: sending observations, assigning vessels to subject groups, and deactivating stale subjects when vessels go off-radar.

## Architecture Overview
```
app/
  actions/
    configurations.py       # Pydantic config models for each action
    handlers.py             # Action handlers (pull + 3 debug actions)
    core.py                 # Base config classes, action discovery
    marine_monitor/
      client.py             # MarineMonitorClient (async httpx wrapper)
      errors.py             # Exception hierarchy
    tests/
      conftest.py           # Fixtures + patch_handler_dependencies()
      test_handlers.py      # Handler unit tests
      test_client.py        # Client unit tests
  services/
    state.py                # IntegrationStateManager (Redis-backed)
    gundi.py                # send_observations_to_gundi()
    activity_logger.py      # @activity_logger decorator
    action_scheduler.py     # @crontab_schedule decorator
  settings/
    base.py                 # Redis, Gundi, Keycloak, logging settings
    integration.py          # Integration-specific settings (add custom here)
```

## Key Technologies
- Python 3.10, FastAPI, pydantic v1, Redis
- `httpx` for async HTTP (Marine Monitor client)
- `earthranger-client` → `AsyncERClient` for ER API calls
- `gundi-client-v2` → `GundiClient` for Gundi platform API
- `stamina` for retry logic, `backoff` for per-call retries
- `pytest-asyncio`, `respx`, `unittest.mock` for testing

## Actions

**`action_pull_vessel_tracking`** (every 5 min, `PullVesselTrackingConfiguration`):
1. Fetch radar stations + tracks from Marine Monitor (`MarineMonitorClient.get_track_markers()`)
2. Filter by `minimal_confidence` (default 0.1)
3. Transform to observations and post directly to EarthRanger via `AsyncERClient.post_sensor_observation()`
4. Optionally assign vessels to an ER subject group (`earthranger_subject_group_name`)
5. Compute stale vessel IDs **once before** the destination loop
6. Delete stale vessels from ER + update Redis state

**Debug actions** (manual trigger via portal):

| Action | Key config field | Purpose |
|--------|-----------------|---------|
| `action_get_vessels_state` | — | Inspect Redis vessel state |
| `action_delete_vessel` | `vessel_id` (required) | Delete a specific vessel from ER + state |
| `action_clear_vessel_state` | — | Wipe all vessel state from Redis |

## Conventions
- Use `Optional[str]` (not `str | None`) — pydantic v1 style
- Use `SecretStr` for secrets; access via `.get_secret_value()`
- **Never store ER `base_url` or `token` in action configs** — fetch from destination integration at runtime via `GundiClient`
- Observations are sent **directly to ER** via `AsyncERClient`, not through Gundi's sensor pipeline — this is intentional for this connector
- Stale vessel IDs must be computed once before the destination loop, not inside it
- EarthRanger deletion order: get source by `manufacturer_id` → patch most recent subject `is_active=False` → delete source; skip immediately on `ERClientBadCredentials` / `ERClientPermissionDenied`
- Debug actions need `ExecutableActionMixin` + a dummy `notes: Optional[str]` field so the portal renders the section and shows the trigger button
- Don't update tests during implementation — only when ready to push to GitHub

## State Management
Redis-backed via `IntegrationStateManager`. Key format: `integration_state.{integration_id}.{action_id}.{source_id}`.
- `track_index` source: `{"known_vessels": {track_id: {"last_seen": "..."}}, "last_run": "..."}`
- Per-track source: `{"last_seen": "..."}`

## Development Commands
```bash
# Run tests
pytest app/actions/tests/ -v

# Local dev stack (Redis + PubSub emulator + FastAPI with debugpy on :5678, uvicorn on :8080)
cd local && docker-compose up

# Compile dependencies after editing requirements.in
uv pip compile requirements.in -o requirements.txt
```

Local action testing via Swagger UI at `http://localhost:8080/docs`. `config_overrides` must include **all** config fields (even null ones) or the endpoint returns 404.
