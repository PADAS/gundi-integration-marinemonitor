# Gundi Connector Advanced Reference

Deep-dive technical patterns for advanced connector features.

## Retry Strategy with Stamina

Use the `stamina` library for automatic retries with exponential backoff and jitter.

### Pattern 1: Decorator-Based Retry

```python
import stamina
import httpx

@stamina.retry(
    on=httpx.HTTPError,
    attempts=3,
    wait_initial=1.0,
    wait_jitter=5.0,
    wait_max=32.0
)
async def send_observations_to_gundi(observations: list, integration_id: str):
    """Automatically retries on httpx.HTTPError with exponential backoff."""
    async with GundiDataSenderClient(integration_api_key=api_key) as client:
        return await client.post_observations(data=observations)
```

### Pattern 2: Context Manager Retry

```python
async def fetch_data_with_retry(url: str):
    async for attempt in stamina.retry_context(
        on=(httpx.HTTPError, asyncio.TimeoutError),
        attempts=5,
        wait_initial=1.0,
        wait_jitter=3.0
    ):
        with attempt:
            async with httpx.AsyncClient() as client:
                response = await client.get(url)
                response.raise_for_status()
                return response.json()
```

### When to Use Retry

- **Always**: Sending data to Gundi (network transient failures)
- **Always**: Publishing events to PubSub
- **Consider**: External API calls (check API rate limits first)
- **Never**: Authentication checks (fail fast on bad credentials)

## State Management

Use Redis-based state management to track incremental pulls and avoid reprocessing data.

### State Manager Usage

```python
from app.services.state import IntegrationStateManager

state_manager = IntegrationStateManager()

# Get previous state
state = await state_manager.get_state(
    integration_id=str(integration.id),
    action_id="pull_observations",
    source_id=device_id  # Use device ID for per-device tracking
)

# Example: Get last processed timestamp
last_timestamp = state.get("latest_device_timestamp")

# Update state after successful processing
await state_manager.set_state(
    integration_id=str(integration.id),
    action_id="pull_observations",
    state={"latest_device_timestamp": max_timestamp, "last_run": datetime.now().isoformat()},
    source_id=device_id
)
```

### State Key Format

Keys follow the pattern: `integration_state.{integration_id}.{action_id}.{source_id}`

### Incremental Pulls Pattern

```python
@activity_logger()
async def action_pull_observations(integration, action_config):
    # Get state for each device/source
    devices = await get_devices_from_api()

    for device in devices:
        state = await state_manager.get_state(
            integration_id=str(integration.id),
            action_id="pull_observations",
            source_id=device.id
        )

        # Use state to determine time range
        start_date = state.get("latest_device_timestamp") or (datetime.now() - timedelta(days=7))

        # Fetch only new data
        new_data = await fetch_data_since(device, start_date)

        # Process and send
        await send_observations_to_gundi(observations=new_data, integration_id=str(integration.id))

        # Update state
        if new_data:
            max_timestamp = max(obs["recorded_at"] for obs in new_data)
            await state_manager.set_state(
                integration_id=str(integration.id),
                action_id="pull_observations",
                state={"latest_device_timestamp": max_timestamp},
                source_id=device.id
            )
```

### Pagination State Pattern

```python
# Track pagination cursor
state = await state_manager.get_state(integration_id, action_id)
cursor = state.get("next_cursor")

# Fetch page
results, next_cursor = await api_client.get_page(cursor=cursor)

# Update cursor
await state_manager.set_state(integration_id, action_id, {"next_cursor": next_cursor})
```

## Error Handling Best Practices

### Framework Handles Most Errors Automatically

**IMPORTANT**: The framework (`app/services/action_runner.py`) already handles:
- ✅ Comprehensive error logging with tracebacks
- ✅ Request/response details extraction (from httpx exceptions)
- ✅ Publishing error events to activity logs
- ✅ Proper HTTP status codes (500 for retries, 422 for validation, 404 for missing config)

**DO NOT duplicate this error handling in your action handlers.** Simply let exceptions bubble up to the framework.

### When to Use Custom Activity Logging

Only use `log_action_activity()` or `log_webhook_activity()` when you have **additional context** not available to the framework:

**✅ Good - Adds valuable domain context:**

```python
import logging
from gundi_core.schemas.v2 import LogLevel
from app.services.activity_logger import activity_logger, log_action_activity

logger = logging.getLogger(__name__)

@activity_logger()
async def action_pull_observations(integration, action_config):
    # Log progress with domain-specific information
    devices = await fetch_devices()

    await log_action_activity(
        integration_id=str(integration.id),
        action_id="pull_observations",
        title=f"Processing {len(devices)} devices from API",
        level=LogLevel.INFO,
        data={
            "devices_found": len(devices),
            "filter_applied": action_config.device_filter,
            "lookback_days": action_config.lookback_days
        }
    )

    # Process devices - let exceptions bubble up to framework
    observations = await process_all_devices(devices)

    return {"observations_extracted": len(observations), "devices_processed": len(devices)}
```

**❌ Bad - Duplicates framework error handling:**

```python
# DON'T DO THIS - framework already handles it!
import traceback

@activity_logger()
async def action_pull_observations(integration, action_config):
    try:
        devices = await fetch_devices()
    except Exception as e:
        # WRONG: Framework already logs errors with full context
        await log_action_activity(
            integration_id=str(integration.id),
            action_id="pull_observations",
            title=f"Error fetching devices",
            level=LogLevel.ERROR,
            data={"error": str(e), "traceback": traceback.format_exc()}
        )
        raise  # Framework will catch and log this anyway
```

### Graceful Degradation (Multi-Device Processing)

```python
@activity_logger()
async def action_pull_observations(integration, action_config):
    results = {"observations_extracted": 0, "devices_processed": 0, "devices_failed": 0}

    devices = await get_devices()

    for device in devices:
        try:
            # Process device
            observations = await process_device(device)
            await send_observations_to_gundi(observations, integration_id=str(integration.id))
            results["observations_extracted"] += len(observations)
            results["devices_processed"] += 1
        except Exception as e:
            # Log error but continue with other devices
            logger.warning(f"Failed to process device {device.id}: {e}")
            results["devices_failed"] += 1
            continue

    return results
```

## Key Differences: Pull vs Push vs Webhook

### Pull Actions (Extract data INTO Gundi)

- **Triggered by:** Cron schedule (`@crontab_schedule()`)
- **Data flow:** External API → Connector → Gundi
- **Config:** Single `PullActionConfiguration` with API credentials and extraction settings
- **Handler signature:** `action_pull_*(integration, action_config)`
- **Return:** Dictionary with extraction stats (e.g., `{"observations_extracted": 10}`)
- **Common tasks:**
  - Fetch data from external API with pagination
  - Transform to Gundi schema (Observations/Events/Attachments)
  - Call `send_observations_to_gundi()` or similar
  - Handle lookback periods and state management

### Push Actions (Send data FROM Gundi)

- **Triggered by:** Events from Gundi (data ready to dispatch)
- **Data flow:** Gundi → Connector → External API
- **Config:** Requires TWO configs:
  1. `AuthActionConfiguration` with `ExecutableActionMixin` (credentials)
  2. `PushActionConfiguration` (push-specific settings, can be empty)
- **Handler signature:** `action_push_*(integration, action_config, data, metadata)`
  - `data`: Pre-transformed data from Gundi (e.g., `MessageTransformedInReach`)
  - `metadata`: Contains `gundi_id`, tracking info
- **Return:** Dictionary with dispatch stats (e.g., `{"status": "success"}`)
- **Must implement:** `action_auth()` for credential testing
- **Common tasks:**
  - Retrieve auth config via `integration.get_action_config("auth")`
  - Extract credentials from auth config
  - Send data to external API
  - Log success/failure with `log_action_activity()`
  - Re-raise exceptions to trigger GCP retry

### Webhooks (Receive data PUSHED to Gundi)

- **Triggered by:** HTTP requests from external systems
- **Data flow:** External System → Webhook → Gundi
- **Config:** `WebhookConfiguration` (can use dynamic schemas)
- **Handler signature:** `webhook_handler(payload, integration, webhook_config)`
- **Return:** Dictionary with processing stats
- **Common tasks:**
  - Validate incoming payload with Pydantic
  - Transform to Gundi schema
  - Send to Gundi via `send_observations_to_gundi()`

## Advanced Configuration Options

### Dynamic Schemas

For webhooks with user-defined schemas:

```python
from app.webhooks.core import GenericJsonPayload, DynamicSchemaConfig
```

### JSON Transformations with JQ

```python
from app.webhooks.core import GenericJsonTransformConfig
```

### Hex String Payloads

For binary data:

```python
from app.webhooks.core import HexStringPayload, HexStringConfig
```

### Custom Portal UI

```python
from app.services.utils import FieldWithUIOptions, UIOptions
```
