# Gundi Connector Testing Guide

Comprehensive testing patterns for Gundi connectors.

## Test Structure

```
app/actions/tests/
├── __init__.py
├── conftest.py           # Shared fixtures
├── test_actions.py       # Action handler tests
└── test_client.py        # API client tests
```

## Required Test Dependencies

Add to `requirements.in`:

```text
pytest>=7.0.0
pytest-asyncio>=0.21.0
respx>=0.20.0  # For mocking httpx requests
```

## Test Categories

1. **API Client Tests** - Test HTTP interactions with mocked responses
2. **Action Handler Tests** - Test business logic with mocked dependencies
3. **Configuration Tests** - Validate Pydantic models (optional)

## API Client Tests

```python
# app/actions/tests/test_api_client.py
import httpx
import pytest
import respx
from ..your_api_client import (
    ExternalAPIClient,
    APIAuthenticationError,
    APIServiceUnreachable,
)

@pytest.mark.asyncio
async def test_client_success():
    """Test successful API call."""
    async with respx.mock(assert_all_called=True) as mock:
        mock.post("/data").respond(
            status_code=httpx.codes.OK,
            json={"status": "success"}
        )

        async with ExternalAPIClient(api_url="https://api.example.com", api_key="test") as client:
            response = await client.send_data(payload={"test": "data"})
            assert response == {"status": "success"}

@pytest.mark.asyncio
async def test_client_authentication_error():
    """Test authentication failure."""
    async with respx.mock(assert_all_called=True) as mock:
        mock.post("/data").respond(status_code=httpx.codes.UNAUTHORIZED)

        async with ExternalAPIClient(api_url="https://api.example.com", api_key="bad") as client:
            with pytest.raises(APIAuthenticationError) as exc:
                await client.send_data(payload={"test": "data"})
            assert exc.value.response.status_code == httpx.codes.UNAUTHORIZED

@pytest.mark.asyncio
async def test_client_service_unavailable():
    """Test service unavailable error."""
    async with respx.mock(assert_all_called=True) as mock:
        mock.post("/data").respond(status_code=httpx.codes.SERVICE_UNAVAILABLE)

        async with ExternalAPIClient(api_url="https://api.example.com", api_key="test") as client:
            with pytest.raises(APIServiceUnreachable):
                await client.send_data(payload={"test": "data"})
```

## Test Fixtures (conftest.py)

```python
# app/actions/tests/conftest.py
from unittest.mock import AsyncMock
import pytest
from gundi_core.schemas.v2 import Integration

@pytest.fixture
def mock_integration():
    """Fixture for mock integration object."""
    return Integration.parse_obj({
        "id": "integration-id-123",
        "name": "Test Integration",
        "type": {"id": "type-id", "name": "External API", "value": "external_api"},
        "configurations": [
            {
                "id": "config-id-auth",
                "integration": "integration-id-123",
                "action": {"id": "auth-id", "type": "auth", "value": "auth"},
                "data": {
                    "api_url": "https://api.example.com",
                    "api_key": "test-key"
                }
            },
            {
                "id": "config-id-push",
                "integration": "integration-id-123",
                "action": {"id": "push-id", "type": "push", "value": "push_messages"},
                "data": {}
            }
        ],
        "base_url": "",
        "enabled": True,
        "owner": {"id": "owner-id", "name": "Test Org", "description": ""},
        "additional": {},
        "status": "healthy"
    })

@pytest.fixture
def mock_api_client(mocker):
    """Mock API client."""
    mock_client = mocker.MagicMock()
    mock_client.send_data = AsyncMock(return_value={"status": "success"})
    mock_client.test_connection = AsyncMock(return_value={})
    mock_client.__aenter__.return_value = mock_client
    return mock_client

@pytest.fixture
def mock_api_client_class(mocker, mock_api_client):
    """Mock API client class."""
    mock_class = mocker.MagicMock()
    mock_class.return_value = mock_api_client
    return mock_class

@pytest.fixture
def mock_push_data():
    """Mock data for push actions."""
    return {
        "event_id": "event-123",
        "timestamp": "2025-01-08T10:00:00+00:00",
        "payload": {"message": "Test message"},
        "event_type": "MessageTransformed"
    }

@pytest.fixture
def mock_metadata():
    """Mock metadata for push actions."""
    return {
        "gundi_id": "gundi-123",
        "destination_id": "integration-id-123",
        "source_id": "source-123"
    }
```

## Action Handler Tests

```python
# app/actions/tests/test_actions.py
import pytest
from unittest.mock import AsyncMock
from gundi_core.schemas.v2 import LogLevel
from app.services.action_runner import execute_action
from ..your_api_client import APIAuthenticationError, APIServiceUnreachable

@pytest.mark.asyncio
async def test_auth_action_valid_credentials(
    mocker, mock_integration, mock_api_client_class, mock_config_manager
):
    """Test authentication with valid credentials."""
    mocker.patch("app.actions.handlers.ExternalAPIClient", mock_api_client_class)
    mocker.patch("app.services.action_runner.config_manager", mock_config_manager)

    response = await execute_action(
        integration_id=str(mock_integration.id),
        action_id="auth",
        config_overrides={"api_url": "https://api.example.com", "api_key": "test"}
    )

    assert response.get("valid_credentials") is True

@pytest.mark.asyncio
async def test_auth_action_invalid_credentials(
    mocker, mock_integration, mock_api_client_class, mock_config_manager
):
    """Test authentication with invalid credentials."""
    mock_api_client_class.return_value.test_connection = AsyncMock(
        side_effect=APIAuthenticationError()
    )
    mocker.patch("app.actions.handlers.ExternalAPIClient", mock_api_client_class)
    mocker.patch("app.services.action_runner.config_manager", mock_config_manager)

    response = await execute_action(
        integration_id=str(mock_integration.id),
        action_id="auth",
        config_overrides={"api_url": "https://api.example.com", "api_key": "bad"}
    )

    assert response.get("valid_credentials") is False

@pytest.mark.asyncio
async def test_push_action_success(
    mocker, mock_integration, mock_api_client_class,
    mock_config_manager, mock_push_data, mock_metadata
):
    """Test successful push action."""
    mocker.patch("app.actions.handlers.ExternalAPIClient", mock_api_client_class)
    mocker.patch("app.services.action_runner.config_manager", mock_config_manager)
    mock_log_activity = AsyncMock()
    mocker.patch("app.actions.handlers.log_action_activity", mock_log_activity)

    response = await execute_action(
        integration_id=str(mock_integration.id),
        action_id="push_messages",
        data=mock_push_data,
        metadata=mock_metadata
    )

    assert response.get("status") == "success"

    # Verify success was logged
    mock_log_activity.assert_awaited_once()
    call_kwargs = mock_log_activity.mock_calls[0].kwargs
    assert call_kwargs.get("level") == LogLevel.DEBUG
    assert "delivered_at" in call_kwargs.get("data", {})

@pytest.mark.asyncio
async def test_push_action_error(
    mocker, mock_integration, mock_api_client_class,
    mock_config_manager, mock_push_data, mock_metadata
):
    """Test push action with API error."""
    mock_api_client_class.return_value.send_data = AsyncMock(
        side_effect=APIServiceUnreachable()
    )
    mocker.patch("app.actions.handlers.ExternalAPIClient", mock_api_client_class)
    mocker.patch("app.services.action_runner.config_manager", mock_config_manager)
    mock_log_activity = AsyncMock()
    mocker.patch("app.actions.handlers.log_action_activity", mock_log_activity)

    response = await execute_action(
        integration_id=str(mock_integration.id),
        action_id="push_messages",
        data=mock_push_data,
        metadata=mock_metadata
    )

    # Should return 500 for retry
    assert response.status_code == 500

    # Verify error was logged
    mock_log_activity.assert_awaited_once()
    call_kwargs = mock_log_activity.mock_calls[0].kwargs
    assert call_kwargs.get("level") == LogLevel.ERROR
    assert "error" in call_kwargs.get("data", {})
    assert "error_traceback" in call_kwargs.get("data", {})
```

## Running Tests
Always run tests in a .venv with python 3.10

```bash
# Run all tests with verbose output
pytest -v

# Run specific test file
pytest app/actions/tests/test_actions.py -v

# Run specific test
pytest app/actions/tests/test_actions.py::test_auth_action_valid_credentials -v

# Run with coverage
pytest --cov=app/actions --cov-report=html

# Run async tests only
pytest -k asyncio -v
```

## Test Coverage Goals

- **API Client:** 100% coverage (all methods, all error paths)
- **Action Handlers:** All success and error scenarios
- **Authentication:** Valid/invalid credentials, service errors
- **Push Actions:** Success, errors, logging verification
- **Pull Actions:** Data extraction, pagination, transformation
- **State Management:** Get/set state, per-device tracking

## Testing Best Practices

1. **Mock external dependencies** - Use `respx` for HTTP, `mocker` for services
2. **Test error paths** - Not just success scenarios
3. **Verify activity logging** - Check that logs are created with correct data
4. **Test state management** - Ensure incremental pulls work correctly
5. **Use fixtures** - Share common test data via conftest.py
6. **Async tests** - Always mark with `@pytest.mark.asyncio`
7. **Assert specifics** - Check actual values, not just that something returned
8. **Test retry behavior** - Verify stamina retry logic works
