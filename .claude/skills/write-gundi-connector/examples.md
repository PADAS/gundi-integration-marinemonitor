# Gundi Connector Code Examples

Copy-paste ready code templates for implementing connectors.

## Handler Function Signatures

### Pull Action

```python
from app.actions.core import PullActionConfiguration
from app.services.activity_logger import activity_logger, crontab_schedule
from app.services.gundi import send_observations_to_gundi

@crontab_schedule("0 */4 * * *")
@activity_logger()
async def action_pull_observations(integration, action_config: PullActionConfiguration):
    """Pull observations from external API."""
    # Extract data from external API
    # Transform to Gundi format
    # Send to Gundi
    await send_observations_to_gundi(observations=data, integration_id=integration.id)
    return {"observations_extracted": count}
```

### Webhook Handler

```python
from app.webhooks.core import WebhookPayload, WebhookConfiguration
from app.services.activity_logger import webhook_activity_logger
from app.services.gundi import send_observations_to_gundi

class MyWebhookPayload(WebhookPayload):
    device_id: str
    timestamp: str
    lat: float
    lon: float

@webhook_activity_logger()
async def webhook_handler(payload: MyWebhookPayload, integration=None, webhook_config: WebhookConfiguration = None):
    """Process incoming webhook."""
    # Transform to Gundi format
    # Send to Gundi
    await send_observations_to_gundi(observations=data, integration_id=integration.id)
    return {"observations_processed": count}
```

### Push Action

```python
import logging
import traceback
from datetime import datetime
from gundi_core.schemas.v2 import Integration, LogLevel
from app.services.activity_logger import activity_logger, log_action_activity

logger = logging.getLogger(__name__)

@activity_logger()
async def action_push_messages(
    integration: Integration,
    action_config: PushActionConfiguration,
    data: DataSchemaFromGundi,  # Use appropriate Gundi schema
    metadata: dict
):
    """Push action to send data from Gundi to external system."""
    gundi_id = metadata.get("gundi_id")

    # Get authentication config
    auth_config = integration.get_action_config("auth")
    if not auth_config:
        raise ValueError("Authentication configuration is required.")
    parsed_auth_config = AuthenticateConfig.parse_obj(auth_config.data)

    # Extract credentials
    api_url = parsed_auth_config.api_url
    api_key = parsed_auth_config.api_key.get_secret_value()

    try:
        # Use client to send data to external system
        async with ExternalAPIClient(api_url=api_url, api_key=api_key) as client:
            response = await client.send_data(payload=data.payload)
    except Exception as e:
        # Special case: Push actions log extra metadata for tracking
        error = f"{type(e).__name__}: {e}"
        error_title = f"Error Delivering Data {gundi_id} to '{api_url}'"
        logger.exception(f"{error_title}: {error}")

        await log_action_activity(
            integration_id=str(integration.id),
            action_id="push_messages",
            title=error_title,
            level=LogLevel.ERROR,
            data={"error": error, "error_traceback": traceback.format_exc(), **metadata}
        )
        raise  # Re-raise to trigger retry in GCP
    else:
        # Log success with delivery confirmation
        await log_action_activity(
            integration_id=str(integration.id),
            action_id="push_messages",
            title=f"Data {gundi_id} Delivered to '{api_url}'",
            level=LogLevel.DEBUG,
            data={"delivered_at": datetime.now().isoformat(), **metadata}
        )
        return {"status": "success", "response": response}
```

### Authentication Test Action (for push actions)

```python
from gundi_core.schemas.v2 import Integration

async def action_auth(integration: Integration, action_config: AuthenticateConfig):
    """Test authentication credentials."""
    api_url = action_config.api_url
    api_key = action_config.api_key.get_secret_value()

    if not api_url or not api_key:
        return {"valid_credentials": False, "error": "API URL and key are required."}

    try:
        async with ExternalAPIClient(api_url=api_url, api_key=api_key) as client:
            await client.test_connection()
    except AuthenticationError as e:
        return {"valid_credentials": False, "error": str(e)}
    except Exception as e:
        return {"valid_credentials": False, "error": f"Error: {type(e).__name__}: {e}"}
    else:
        return {"valid_credentials": True}
```

## API Client Implementation

### Client Structure

```
app/actions/
├── your_api_client/
│   ├── __init__.py       # Export client and errors
│   ├── client.py         # Main client class
│   └── errors.py         # Custom exceptions
├── configurations.py
└── handlers.py
```

### Client Class (client.py)

```python
import httpx
from typing import Optional
from urllib.parse import urljoin
from .errors import APIClientError, APIAuthenticationError, APIServiceUnreachable

class ExternalAPIClient:
    DEFAULT_CONNECT_TIMEOUT = 10
    DEFAULT_DATA_TIMEOUT = 60

    def __init__(
        self,
        api_url: str,
        api_key: Optional[str] = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        data_timeout: float = DEFAULT_DATA_TIMEOUT,
    ):
        self.api_url = api_url
        self.api_key = api_key
        session_kwargs = {
            "base_url": self.api_url,
            "timeout": httpx.Timeout(data_timeout, connect=connect_timeout),
            "headers": {"Authorization": f"Bearer {api_key}"} if api_key else {}
        }
        self.session = httpx.AsyncClient(**session_kwargs)

    async def __aenter__(self):
        await self.session.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.close()

    async def close(self):
        await self.session.aclose()

    async def _call_api(self, endpoint: str, method: str = "GET", data: dict = None, **kwargs):
        """Make an API call to the external service."""
        url = urljoin(self.api_url, endpoint.lstrip("/"))
        try:
            if method == "GET":
                response = await self.session.get(url, **kwargs)
            elif method == "POST":
                response = await self.session.post(url, json=data, **kwargs)
            elif method == "PUT":
                response = await self.session.put(url, json=data, **kwargs)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            response.raise_for_status()

            if not response.text:
                return {}
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code in [401, 403]:
                raise APIAuthenticationError(response=e.response)
            elif e.response.status_code in [502, 503, 504]:
                raise APIServiceUnreachable(response=e.response)
            else:
                raise APIClientError(f"HTTP {e.response.status_code}: {e.response.text}", response=e.response)
        except httpx.RequestError as e:
            raise APIServiceUnreachable(f"Failed to connect: {type(e).__name__}: {str(e)}")
        except Exception as e:
            raise APIClientError(f"{type(e).__name__}: {str(e)}")

    async def test_connection(self):
        """Test API connection/authentication."""
        return await self._call_api("/health", method="GET")

    async def send_data(self, payload: dict):
        """Send data to external API."""
        return await self._call_api("/data", method="POST", data=payload)
```

### Error Classes (errors.py)

```python
class APIClientError(Exception):
    """Base exception for API client errors."""
    def __init__(self, message=None, response=None):
        super().__init__(message)
        self.response = response

class APIServiceUnreachable(APIClientError):
    def __init__(self, message="Service is currently unavailable.", response=None):
        super().__init__(message, response)

class APIAuthenticationError(APIClientError):
    def __init__(self, message="Invalid credentials.", response=None):
        super().__init__(message, response)

class APIRateLimitError(APIClientError):
    def __init__(self, message="Rate limit exceeded.", response=None):
        super().__init__(message, response)
```

### Error Detail Extraction Utility

```python
# Add to actions/utils.py
def extract_error_details(e: Exception) -> dict:
    """Extract request/response details from exception for logging."""
    error_details = {}
    if (request := getattr(e, "request", None)) is not None:
        error_details.update({
            "request_verb": str(request.method),
            "request_url": str(request.url),
            "request_data": str(getattr(request, "content", None) or "")
        })
    if (response := getattr(e, "response", None)) is not None:
        error_details.update({
            "server_response_status": getattr(response, "status_code", None),
            "server_response_body": str(getattr(response, "text", None) or "")
        })
    return error_details
```

## Data Transformation Examples

### Field Mapping and Unit Conversion

```python
def transform_to_gundi_observation(external_data: dict, device_info: dict) -> dict:
    """Transform external format to Gundi observation schema."""
    observation = {
        "source": external_data.get("device_id") or device_info.get("serial_number"),
        "type": "tracking-device",
        "subject_type": device_info.get("animal_type", "wildlife"),
        "recorded_at": external_data["timestamp"],  # Must be ISO 8601
        "location": {
            "lat": float(external_data["latitude"]),
            "lon": float(external_data["longitude"])
        },
        "additional": {}
    }

    # Add optional fields with unit conversion
    if "speed" in external_data:
        observation["additional"]["speed_kmph"] = external_data["speed"] * 3.6  # m/s to km/h

    if "temperature" in external_data:
        temp_c = (external_data["temperature"] - 32) * 5/9  # F to C
        observation["additional"]["temperature_celsius"] = round(temp_c, 2)

    # Add all other sensor readings
    observation["additional"].update({
        k: v for k, v in external_data.items()
        if k not in ["device_id", "timestamp", "latitude", "longitude"]
    })

    return observation
```

### JQ-Based Transformations (for webhooks)

```python
import json
import pyjq
from app.services.activity_logger import webhook_activity_logger
from app.services.gundi import send_observations_to_gundi, send_events_to_gundi
from .core import GenericJsonPayload, GenericJsonTransformConfig

@webhook_activity_logger()
async def webhook_handler(
    payload: GenericJsonPayload,
    integration=None,
    webhook_config: GenericJsonTransformConfig = None
):
    """Apply JQ filter from user configuration."""
    input_data = json.loads(payload.json())
    filter_expression = webhook_config.jq_filter.replace("\n", "").replace(" ", "")
    transformed_data = pyjq.all(filter_expression, input_data)

    # Send based on output type
    if webhook_config.output_type == "obv":
        await send_observations_to_gundi(
            observations=transformed_data,
            integration_id=integration.id
        )
    elif webhook_config.output_type == "ev":
        await send_events_to_gundi(
            events=transformed_data,
            integration_id=integration.id
        )

    return {"data_points_processed": len(transformed_data) if isinstance(transformed_data, list) else 1}
```
