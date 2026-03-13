# Plan: Remove earthranger_base_url/token + Add Subject Group Assignment

## Context

`PullVesselTrackingConfiguration` currently stores `earthranger_base_url` and `earthranger_token` — these are wrong because the destination's URL and auth token are already stored on the destination's `Integration` record in the platform (`base_url` + `auth` action config with `token`). Additionally, a new feature is needed: when a vessel track is seen for the first time, the corresponding EarthRanger subject should be assigned to a configured subject group.

This work spans two repos:
- **gundi-integration-marinemonitor** (this repo) — main connector changes
- **er-client** (`/Users/victorl/Documents/Code/er-client`) — add `patch_subjectgroup` to `AsyncERClient`

---

## Repo 1: er-client

### File: `erclient/client.py`

Add `patch_subjectgroup` to `AsyncERClient` (after the existing `patch_subject` method ~line 1365):

```python
async def patch_subjectgroup(self, group_id, data):
    """
    Update a subject group with partial data (e.g., add/remove subjects).

    :param group_id: The subject group UUID
    :param data: Partial subject group data (e.g., {"subjects": [{"id": "..."}]})
    :return: Updated subject group data
    """
    self.logger.debug(f'Patching subjectgroup {group_id}: {data}')
    return await self._patch(f'subjectgroup/{group_id}', payload=data)
```

### File: `tests/async_client/test_patch_subjectgroup.py` (new file)

Add tests following the pattern of `test_patch_subject.py` using `respx` mocks:
- `test_patch_subjectgroup_success` — 200 OK, verifies response
- `test_patch_subjectgroup_not_found` — 404 raises `ERClientNotFound`
- `test_patch_subjectgroup_bad_credentials` — 401 raises `ERClientBadCredentials`
- `test_patch_subjectgroup_permission_denied` — 403 raises `ERClientPermissionDenied`
- `test_patch_subjectgroup_internal_error` — 500 raises `ERClientInternalError`

---

## Repo 2: gundi-integration-marinemonitor (this repo)

### Files to Modify
- `app/actions/configurations.py`
- `app/actions/handlers.py`
- `app/actions/tests/conftest.py`
- `app/actions/tests/test_handlers.py`

---

### 1. `app/actions/configurations.py`

- Remove `earthranger_base_url` (lines 34–42) and `earthranger_token` (lines 44–48) from `PullVesselTrackingConfiguration`
- Add optional `earthranger_subject_group` field:

```python
earthranger_subject_group: Optional[str] = Field(
    None,
    title="EarthRanger Subject Group",
    description="Name of the EarthRanger subject group to assign new vessel subjects to.",
)
```

---

### 2. `app/actions/handlers.py`

**New imports** (add if not already present):
```python
import httpx
import stamina
from gundi_client_v2 import GundiClient
```

**New helper function** `_assign_new_subjects_to_group` (add near `_handle_stale_subjects`):

```python
async def _assign_new_subjects_to_group(
    state_manager: IntegrationStateManager,
    integration_id: str,
    er_base_url: str,
    er_token: str,
    group_name: str,
    active_track_ids: set[str],
) -> int:
    """Assign subjects for new tracks (first appearance) to an EarthRanger subject group.

    Returns count of subjects successfully assigned.
    """
    # Get known tracks from state to identify new tracks
    index_state = await state_manager.get_state(
        integration_id=integration_id,
        action_id="pull_vessel_tracking",
        source_id="track_index",
    )
    known_track_ids = set(index_state.get("track_ids", []))
    new_track_ids = active_track_ids - known_track_ids

    if not new_track_ids:
        return 0

    assigned_count = 0
    async with AsyncERClient(
        service_root=f"{er_base_url}/api/v1.0",
        token=er_token,
    ) as client:
        # Find the subject group by name
        groups = await client.get_subjectgroups(group_name=group_name, flat=True)
        matching = [g for g in groups if g.get("name") == group_name]
        if not matching:
            logger.warning(f"Subject group '{group_name}' not found in EarthRanger, skipping assignment")
            return 0
        group_id = matching[0].get("id")

        for track_id in new_track_ids:
            try:
                source_response = await client.get_source_by_manufacturer_id(track_id)
                source = source_response.get("data", source_response)
                source_id = source.get("id")
                if not source_id:
                    continue
                subjects = await client.get_source_subjects(source_id)
                if not subjects:
                    continue
                subject_id = subjects[0].get("id")  # Pick most relevant subject
                if not subject_id:
                    continue
                await client.patch_subjectgroup(group_id, {"subjects": [{"id": subject_id}]})
                logger.info(f"Assigned subject '{subject_id}' for track '{track_id}' to group '{group_name}'")
                assigned_count += 1
            except Exception as e:
                logger.warning(f"Failed to assign track '{track_id}' to group '{group_name}': {e}")

    return assigned_count
```

**Updated `action_pull_vessel_tracking`**: Replace the current `deactivate_subjects_auto` block (lines 343–351) with a combined block that fetches destination credentials once and handles both features:

```python
if action_config.deactivate_subjects_auto or action_config.earthranger_subject_group:
    async with GundiClient() as gundi:
        async for attempt in stamina.retry_context(on=httpx.HTTPError, wait_initial=1.0, wait_jitter=5.0, wait_max=32.0):
            with attempt:
                connection = await gundi.get_connection_details(integration_id)
    for destination in (connection.destinations or []):
        dest_base_url = destination.base_url
        async with GundiClient() as gundi:
            async for attempt in stamina.retry_context(on=httpx.HTTPError, wait_initial=1.0, wait_jitter=5.0, wait_max=32.0):
                with attempt:
                    dest_integration = await gundi.get_integration_details(str(destination.id))
        auth_config = dest_integration.get_action_config("auth")
        if not auth_config or not dest_base_url:
            logger.warning(f"Destination {destination.id} missing base_url or auth config, skipping")
            continue
        dest_token = auth_config.data.get("token")
        if not dest_token:
            logger.warning(f"Destination {destination.id} auth config missing token, skipping")
            continue

        if action_config.earthranger_subject_group:
            await _assign_new_subjects_to_group(
                state_manager=state_manager,
                integration_id=integration_id,
                er_base_url=dest_base_url,
                er_token=dest_token,
                group_name=action_config.earthranger_subject_group,
                active_track_ids=active_track_ids,
            )

        if action_config.deactivate_subjects_auto:
            results["subjects_deactivated"] += await _handle_stale_subjects(
                state_manager=state_manager,
                integration_id=integration_id,
                er_base_url=dest_base_url,
                er_token=dest_token,
                active_track_ids=active_track_ids,
                now=now,
            )
```

The `_handle_stale_subjects` and `deactivate_subject_in_earthranger` functions require **no changes**.

---

### 3. `app/actions/tests/conftest.py`

- Remove `earthranger_base_url` (line 24) and `earthranger_token` (line 25) from `mock_pull_config`
- Add `earthranger_subject_group = "Marine Monitor Vessels"` to `mock_pull_config`
- Add fixtures:

```python
@pytest.fixture
def mock_destination():
    dest = MagicMock()
    dest.id = "dest-integration-id-456"
    dest.base_url = "https://gundi-dev.staging.pamdas.org"
    return dest

@pytest.fixture
def mock_connection(mock_destination):
    conn = MagicMock()
    conn.destinations = [mock_destination]
    return conn

@pytest.fixture
def mock_dest_integration():
    integration = MagicMock()
    auth_config = MagicMock()
    auth_config.data = {"token": "test-er-token"}
    integration.get_action_config = MagicMock(return_value=auth_config)
    return integration
```

- Update `patch_handler_dependencies` to also patch `GundiClient` returning `mock_connection` and `mock_dest_integration`, and patch `_assign_new_subjects_to_group` and `_handle_stale_subjects` as `AsyncMock`s to avoid live ER API calls.

---

### 4. `app/actions/tests/test_handlers.py`

- Tests for `deactivate_subject_in_earthranger` (lines 200–258) pass `er_base_url`/`er_token` directly — **no changes needed**
- Tests for `action_pull_vessel_tracking` need `GundiClient` mocked (via `patch_handler_dependencies`) to return the connection/destination fixtures
- Add new tests for `_assign_new_subjects_to_group`: success case, group not found, source not found

---

## Verification Checklist

1. In `er-client`: run `pytest tests/async_client/test_patch_subjectgroup.py`
2. In `gundi-integration-marinemonitor`: run `pytest app/actions/tests/`
3. Confirm no references to `earthranger_base_url` or `earthranger_token` remain in non-test code
4. Integration test: verify subjects are assigned to the correct group on first appearance and deactivated when stale
