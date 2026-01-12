"""Shared test fixtures for Marine Monitor integration tests."""
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_integration():
    """Fixture for mock integration object."""
    integration = MagicMock()
    integration.id = "test-integration-id-123"
    return integration


@pytest.fixture
def mock_pull_config():
    """Fixture for mock pull action configuration."""
    config = MagicMock()
    config.api_url = "https://m2mobile.protectedseas.net/api/map/42/earthranger"
    config.api_key.get_secret_value.return_value = "test-api-key"
    config.deactivate_subjects_auto = True
    config.earthranger_base_url = "https://gundi-dev.staging.pamdas.org"
    config.earthranger_token.get_secret_value.return_value = "test-er-token"
    return config


@pytest.fixture
def sample_radar_station_response():
    """Sample response from Marine Monitor API with radar stations and tracks."""
    return [
        {
            "id": 42,
            "name": "Loreto 2",
            "latitude": 25.566275,
            "longitude": -111.149116,
            "status": "active",
            "tracks": [
                {
                    "id": 48590736,
                    "source": "ais",
                    "radar_track_id": 538071772,
                    "radar_id": 42,
                    "last_update": "2026-01-09T12:19:23Z",
                    "started": "2026-01-09T10:00:23Z",
                    "confidence": 1.0,
                    "active": 1,
                    "vessel_name": "Test Vessel",
                    "track_detection": {
                        "server_track_id": 48590736,
                        "radar_id": 42,
                        "radar_track_id": 538071772,
                        "timestamp": "2026-01-09T12:19:23Z",
                        "bearing": 330.02509623891,
                        "distance": 17.023913147658,
                        "speed": 0.0,
                        "heading": 356.0,
                        "confidence": 0,
                        "lat": 25.811533,
                        "lon": -111.306303,
                    },
                }
            ],
        },
        {
            "id": 23,
            "name": "Loreto",
            "latitude": 26.015,
            "longitude": -111.3403,
            "status": "active",
            "tracks": [],  # No tracks
        },
    ]


@pytest.fixture
def sample_track():
    """Sample track data from Marine Monitor."""
    return {
        "id": 48590736,
        "source": "ais",
        "radar_track_id": 538071772,
        "radar_id": 42,
        "last_update": "2026-01-09T12:19:23Z",
        "started": "2026-01-09T10:00:23Z",
        "confidence": 1.0,
        "active": 1,
        "vessel_name": "Test Vessel",
        "track_detection": {
            "server_track_id": 48590736,
            "radar_id": 42,
            "radar_track_id": 538071772,
            "timestamp": "2026-01-09T12:19:23Z",
            "bearing": 330.025,
            "distance": 17.024,
            "speed": 5.5,
            "heading": 356.0,
            "lat": 25.811533,
            "lon": -111.306303,
        },
    }


@pytest.fixture
def sample_radar_station():
    """Sample radar station data."""
    return {
        "id": 42,
        "name": "Loreto 2",
        "latitude": 25.566275,
        "longitude": -111.149116,
    }


@pytest.fixture
def mock_state_manager():
    """Fixture for mock state manager."""
    state_manager = MagicMock()
    state_manager.get_state = AsyncMock(return_value={})
    state_manager.set_state = AsyncMock()
    return state_manager


def create_mock_client(api_response: list) -> MagicMock:
    """Create a mock Marine Monitor client with the given API response."""
    client = MagicMock()
    client.get_track_markers = AsyncMock(return_value=api_response)
    client.test_connection = AsyncMock(return_value=True)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


@pytest.fixture
def mock_marine_monitor_client(sample_radar_station_response):
    """Fixture for mock Marine Monitor client."""
    return create_mock_client(sample_radar_station_response)


@contextmanager
def patch_handler_dependencies(mock_client, mock_state_manager):
    """Context manager to patch all handler dependencies at once.

    Yields a dict with send_observations and log_activity mocks for assertions.
    """
    with patch(
        "app.actions.handlers.MarineMonitorClient",
        return_value=mock_client,
    ), patch(
        "app.actions.handlers.IntegrationStateManager",
        return_value=mock_state_manager,
    ), patch(
        "app.actions.handlers.send_observations_to_gundi",
        new_callable=AsyncMock,
    ) as mock_send, patch(
        "app.actions.handlers.log_action_activity",
        new_callable=AsyncMock,
    ), patch(
        "app.services.activity_logger.publish_event",
        new_callable=AsyncMock,
    ):
        yield {"send_observations": mock_send}
