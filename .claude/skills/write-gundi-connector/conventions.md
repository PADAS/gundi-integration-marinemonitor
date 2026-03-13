# Gundi Connector Conventions & Standards

> **Framework Architecture**: [README.md](../../../README.md)

## Framework Rules

- **DO NOT modify:** `app/services/` (framework code)
- **Implement in:** `app/actions/` or `app/webhooks/` modules only
- **API Clients:** Encapsulate external API calls in a client module (e.g., `app/actions/client.py`)
- **Dependencies:** Add to `requirements.in` (NOT `requirements-base.in`)

## Code Style

- Follow PEP8 conventions
- Write concise, pythonic code
- Use async/await for all I/O operations
- NO sync libraries for I/O (blocks event loop)
- Always use `httpx` (async) for HTTP requests, never `requests` (sync)

## Best Practices

1. Use `@activity_logger()` or `@webhook_activity_logger()` decorators
2. Use `log_action_activity()` or `log_webhook_activity()` for custom logging (only when adding context)
3. Use `@crontab_schedule()` for scheduled pull actions
4. Validate and parse data using Pydantic models
5. Handle pagination for APIs returning large datasets
6. Implement proper error handling (let exceptions bubble to framework)
7. Transform third-party data to match Gundi schemas exactly
8. Use `@stamina.retry()` decorator for sending data to Gundi
9. Implement state management for pull actions to avoid reprocessing data

## Gundi Data Schemas

### Observation Example

```python
{
    "source": "device-id",
    "type": "tracking-device",
    "subject_type": "elephant",
    "recorded_at": "2024-01-24T09:03:00-03:00",
    "location": {
        "lat": -51.748,
        "lon": -72.720
    },
    "additional": {
        "speed_kmph": 10,
        "custom_field": "value"
    }
}
```

### Event Example

```python
{
    "id": "event-123",
    "title": "Poaching Alert",
    "event_type": "security_incident",
    "recorded_at": "2024-01-24T09:03:00-03:00",
    "location": {
        "lat": -51.748,
        "lon": -72.720
    },
    "event_details": {
        "severity": "high"
    }
}
```

## Configuration Base Classes

### Pull Actions

```python
from app.actions.core import PullActionConfiguration

class MyPullConfig(PullActionConfiguration):
    api_key: pydantic.SecretStr
    lookback_days: int = 7
```

### Webhooks

```python
from app.webhooks.core import WebhookPayload, WebhookConfiguration

class MyWebhookPayload(WebhookPayload):
    device_id: str
    timestamp: str
    lat: float
    lon: float

class MyWebhookConfig(WebhookConfiguration):
    field_mapping: dict
```

### Push Actions

```python
from pydantic import Field, SecretStr
from app.actions.core import PushActionConfiguration, AuthActionConfiguration, ExecutableActionMixin
from app.services.utils import GlobalUISchemaOptions

# Authentication configuration (required for push actions)
class AuthenticateConfig(AuthActionConfiguration, ExecutableActionMixin):
    api_url: str = Field(title="API Base URL", description="Base URL for external API")
    api_key: SecretStr = Field(title="API Key", description="API key for authentication", format="password")

    ui_global_options: GlobalUISchemaOptions = GlobalUISchemaOptions(
        order=["api_url", "api_key"]
    )

# Push action configuration (can be empty if auth config has everything)
class MyPushConfig(PushActionConfiguration):
    pass
```

## Common Dependencies

Add to `requirements.in`:

```text
httpx>=0.24.0           # Async HTTP client
stamina>=24.0.0         # Retry with exponential backoff
pytest>=7.0.0           # Testing framework
pytest-asyncio>=0.21.0  # Async test support
respx>=0.20.0           # Mock httpx requests
pyjq>=2.6.0             # JQ transformations (if needed)
```

## Additional Framework Features

- **Dynamic schemas**: Use `GenericJsonPayload` and `DynamicSchemaConfig`
- **JSON transformations**: Use `GenericJsonTransformConfig` with JQ filters
- **Hex string payloads**: Use `HexStringPayload` and `HexStringConfig`
- **Portal UI customization**: Use `FieldWithUIOptions` and `UIOptions`
- **Framework utilities**: Available in `app.services.gundi` and `app.services.utils`
