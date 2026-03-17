"""Tests for Marine Monitor action handlers."""
import pytest
from datetime import timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.actions.handlers import (
    transform_track_to_observation,
    parse_timestamp,
    action_pull_vessel_tracking,
    deactivate_subject_in_earthranger,
    _process_track,
)
from app.actions.tests.conftest import create_mock_client, patch_handler_dependencies


class TestTransformTrackToObservation:
    """Tests for transform_track_to_observation function."""

    def test_transform_basic_track(self, sample_track, sample_radar_station):
        """Test transformation of a basic track to observation."""
        observation = transform_track_to_observation(sample_track, sample_radar_station)

        assert observation["source"] == "vessel-48590736"  # track id with prefix
        assert observation["subject_name"] == "vessel-48590736 (Test Vessel)"
        assert observation["type"] == "tracking-device"
        assert observation["subject_type"] == "vehicle"
        assert observation["recorded_at"] == "2026-01-09T12:19:23Z"
        assert observation["location"]["lat"] == 25.811533
        assert observation["location"]["lon"] == -111.306303

    def test_transform_track_with_vessel_name_uses_vessel_name_as_subject_name(self, sample_radar_station):
        """Test that vessel_name is used as subject_name when present."""
        track = {
            "id": 48590736,
            "vessel_name": "My Vessel",
            "last_update": "2026-01-09T12:00:00Z",
            "track_detection": {"lat": 25.0, "lon": -111.0},
        }
        observation = transform_track_to_observation(track, sample_radar_station)
        assert observation["subject_name"] == "vessel-48590736 (My Vessel)"

    def test_transform_track_without_vessel_name_falls_back_to_manufacturer_id(self, sample_radar_station):
        """Test that subject_name falls back to manufacturer_id when vessel_name is empty."""
        track = {
            "id": 48590736,
            "vessel_name": "",
            "last_update": "2026-01-09T12:00:00Z",
            "track_detection": {"lat": 25.0, "lon": -111.0},
        }
        observation = transform_track_to_observation(track, sample_radar_station)
        assert observation["subject_name"] == "vessel-48590736"

    def test_transform_includes_track_detection_fields(self, sample_track, sample_radar_station):
        """Test that track_detection fields are included in additional."""
        observation = transform_track_to_observation(sample_track, sample_radar_station)

        assert observation["additional"]["speed_kmph"] == 5.5
        assert observation["additional"]["heading"] == 356.0
        assert observation["additional"]["bearing"] == 330.025
        assert observation["additional"]["distance_nm"] == 17.024

    def test_transform_includes_track_metadata(self, sample_track, sample_radar_station):
        """Test that track metadata is included in additional."""
        observation = transform_track_to_observation(sample_track, sample_radar_station)

        assert observation["additional"]["confidence"] == 1.0
        assert observation["additional"]["radar_track_id"] == 538071772
        assert observation["additional"]["vessel_name"] == "Test Vessel"
        assert observation["additional"]["track_source"] == "ais"
        assert observation["additional"]["active"] is True

    def test_transform_includes_radar_station_metadata(self, sample_track, sample_radar_station):
        """Test that radar station metadata is included."""
        observation = transform_track_to_observation(sample_track, sample_radar_station)

        assert observation["additional"]["radar_station_name"] == "Loreto 2"
        assert observation["additional"]["radar_station_id"] == 42

    def test_transform_track_without_id(self, sample_radar_station):
        """Test transformation when id is missing (fallback to default)."""
        track = {
            "last_update": "2026-01-09T12:00:00Z",
            "track_detection": {
                "lat": 25.0,
                "lon": -111.0,
            },
        }

        observation = transform_track_to_observation(track, sample_radar_station)

        assert observation["source"] == "vessel-unknown-source"

    def test_transform_track_uses_last_update_as_fallback(self, sample_radar_station):
        """Test that last_update is used when track_detection.timestamp is missing."""
        track = {
            "id": 12345,
            "radar_track_id": 999,
            "last_update": "2026-01-09T12:00:00Z",
            "track_detection": {
                "lat": 25.0,
                "lon": -111.0,
                # No timestamp in track_detection
            },
        }

        observation = transform_track_to_observation(track, sample_radar_station)

        assert observation["recorded_at"] == "2026-01-09T12:00:00Z"

    def test_transform_track_with_missing_optional_fields(self, sample_radar_station):
        """Test transformation when optional fields are missing."""
        track = {
            "id": 12345,
            "radar_track_id": 999,
            "last_update": "2026-01-09T12:00:00Z",
            "track_detection": {
                "lat": 25.0,
                "lon": -111.0,
            },
        }

        observation = transform_track_to_observation(track, sample_radar_station)

        # Should not have speed, heading, etc. in additional
        assert "speed_kmph" not in observation["additional"]
        assert "heading" not in observation["additional"]
        assert "bearing" not in observation["additional"]
        assert "distance_nm" not in observation["additional"]
        assert "confidence" not in observation["additional"]
        assert "vessel_name" not in observation["additional"]

    def test_process_track_filters_by_confidence(self, sample_radar_station):
        """Test that tracks below minimal confidence are filtered out."""
        track = {
            "id": 12345,
            "confidence": 0.3,
            "last_update": "2026-01-09T12:00:00Z",
            "track_detection": {
                "lat": 25.0,
                "lon": -111.0,
            },
        }

        # With minimal_confidence=0.5, track should be filtered out
        observation = _process_track(track, sample_radar_station, minimal_confidence=0.5)
        assert observation is None

    def test_process_track_passes_confidence_threshold(self, sample_radar_station):
        """Test that tracks meeting minimal confidence are processed."""
        track = {
            "id": 12345,
            "confidence": 0.8,
            "last_update": "2026-01-09T12:00:00Z",
            "track_detection": {
                "lat": 25.0,
                "lon": -111.0,
            },
        }

        # With minimal_confidence=0.5, track should pass
        observation = _process_track(track, sample_radar_station, minimal_confidence=0.5)
        assert observation is not None
        assert observation["source"] == "vessel-12345"

    def test_process_track_no_confidence_field(self, sample_radar_station):
        """Test that tracks without confidence field are not filtered."""
        track = {
            "id": 12345,
            "last_update": "2026-01-09T12:00:00Z",
            "track_detection": {
                "lat": 25.0,
                "lon": -111.0,
            },
        }

        # Without confidence field, track should pass regardless of threshold
        observation = _process_track(track, sample_radar_station, minimal_confidence=0.5)
        assert observation is not None
        assert observation["source"] == "vessel-12345"


class TestParseTimestamp:
    """Tests for parse_timestamp function."""

    def test_parse_timestamp_with_z_suffix(self):
        """Test parsing timestamp with Z suffix."""
        result = parse_timestamp("2026-01-09T12:19:23Z")

        assert result.year == 2026
        assert result.month == 1
        assert result.day == 9
        assert result.hour == 12
        assert result.minute == 19
        assert result.second == 23
        assert result.tzinfo == timezone.utc

    def test_parse_timestamp_with_offset(self):
        """Test parsing timestamp with timezone offset."""
        result = parse_timestamp("2026-01-09T12:19:23+00:00")

        assert result.tzinfo == timezone.utc

    def test_parse_timestamp_without_timezone(self):
        """Test parsing timestamp without timezone assumes UTC."""
        result = parse_timestamp("2026-01-09T12:19:23")

        assert result.tzinfo == timezone.utc

    def test_parse_timestamp_converts_to_utc(self):
        """Test that non-UTC timestamps are converted to UTC."""
        # -07:00 offset (7 hours behind UTC)
        result = parse_timestamp("2026-01-09T05:19:23-07:00")

        # Should be converted to 12:19:23 UTC
        assert result.hour == 12
        assert result.tzinfo == timezone.utc


class TestDeactivateSubjectInEarthRanger:
    """Tests for deactivate_subject_in_earthranger function."""

    @pytest.mark.asyncio
    async def test_deactivate_subject_success(self):
        """Test successful subject deactivation."""
        mock_client = MagicMock()
        mock_client.get_source_by_manufacturer_id = AsyncMock(
            return_value={"data": {"id": "source-uuid-123", "manufacturer_id": "48590736"}}
        )
        mock_client.get_source_subjects = AsyncMock(
            return_value=[
                {
                    "id": "subject-uuid",
                    "is_active": True,
                    "last_position_date": "2026-01-09T12:00:00Z",
                }
            ]
        )
        mock_client.patch_subject = AsyncMock(return_value={"is_active": False})
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("app.actions.handlers.AsyncERClient", return_value=mock_client):
            result = await deactivate_subject_in_earthranger(
                track_id="48590736",
                er_base_url="https://test.pamdas.org",
                er_token="test-token",
            )

        assert result is True
        mock_client.patch_subject.assert_called_once_with("subject-uuid", {"is_active": False})

    @pytest.mark.asyncio
    async def test_deactivate_subject_already_inactive(self):
        """Test when subject is already inactive."""
        mock_client = MagicMock()
        mock_client.get_source_by_manufacturer_id = AsyncMock(
            return_value={"data": {"id": "source-uuid-123"}}
        )
        mock_client.get_source_subjects = AsyncMock(
            return_value=[
                {
                    "id": "subject-uuid",
                    "is_active": False,  # Already inactive
                    "last_position_date": "2026-01-09T12:00:00Z",
                }
            ]
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("app.actions.handlers.AsyncERClient", return_value=mock_client):
            result = await deactivate_subject_in_earthranger(
                track_id="48590736",
                er_base_url="https://test.pamdas.org",
                er_token="test-token",
            )

        assert result is True
        # Should not call patch_subject since already inactive
        mock_client.patch_subject = AsyncMock()
        assert not mock_client.patch_subject.called


class TestActionPullVesselTracking:
    """Tests for action_pull_vessel_tracking handler."""

    @pytest.mark.asyncio
    async def test_pull_vessel_tracking_success(
        self,
        mock_integration,
        mock_pull_config,
        mock_marine_monitor_client,
        mock_state_manager,
    ):
        """Test successful pull of vessel tracking data."""
        with patch_handler_dependencies(
            mock_marine_monitor_client, mock_state_manager
        ) as mocks:
            result = await action_pull_vessel_tracking(
                integration=mock_integration,
                action_config=mock_pull_config,
            )

            assert result["observations_extracted"] == 1
            assert result["radar_stations_processed"] == 2
            assert result["tracks_processed"] == 1
            assert result["radar_stations_failed"] == 0

            # Verify observations were sent to EarthRanger
            assert mocks["er_client"].post_sensor_observation.call_count == 1
            payload = mocks["er_client"].post_sensor_observation.call_args[0][0]
            assert payload["manufacturer_id"] == "vessel-48590736"  # track id with prefix
            assert payload["subject_name"] == "vessel-48590736 (Test Vessel)"

    @pytest.mark.asyncio
    async def test_pull_vessel_tracking_no_tracks(
        self,
        mock_integration,
        mock_pull_config,
        mock_state_manager,
    ):
        """Test pull when no tracks are available."""
        mock_client = create_mock_client([{"id": 1, "name": "Station 1", "tracks": []}])

        with patch_handler_dependencies(mock_client, mock_state_manager) as mocks:
            result = await action_pull_vessel_tracking(
                integration=mock_integration,
                action_config=mock_pull_config,
            )

            assert result["observations_extracted"] == 0
            assert result["tracks_processed"] == 0
            mocks["er_client"].post_sensor_observation.assert_not_called()

    @pytest.mark.asyncio
    async def test_pull_vessel_tracking_api_error(
        self,
        mock_integration,
        mock_pull_config,
        mock_state_manager,
    ):
        """Test that API errors are propagated."""
        from app.actions.marine_monitor import MarineMonitorServiceUnreachable

        mock_client = create_mock_client([])
        mock_client.get_track_markers = AsyncMock(
            side_effect=MarineMonitorServiceUnreachable("Service unavailable")
        )

        with patch_handler_dependencies(mock_client, mock_state_manager):
            with pytest.raises(MarineMonitorServiceUnreachable):
                await action_pull_vessel_tracking(
                    integration=mock_integration,
                    action_config=mock_pull_config,
                )

    @pytest.mark.asyncio
    async def test_pull_vessel_tracking_graceful_degradation(
        self,
        mock_integration,
        mock_pull_config,
        mock_state_manager,
    ):
        """Test that errors for individual radar stations don't stop processing."""
        response = [
            {
                "id": 1,
                "name": "Good Station",
                "tracks": [
                    {
                        "id": 1,
                        "radar_track_id": 111,
                        "track_detection": {
                            "timestamp": "2026-01-09T12:00:00Z",
                            "lat": 25.0,
                            "lon": -111.0,
                        },
                    }
                ],
            },
            {
                # Station without ID should be skipped
                "name": "Bad Station",
                "tracks": [],
            },
        ]

        mock_client = create_mock_client(response)

        with patch_handler_dependencies(mock_client, mock_state_manager):
            result = await action_pull_vessel_tracking(
                integration=mock_integration,
                action_config=mock_pull_config,
            )

            assert result["observations_extracted"] == 1
            assert result["radar_stations_processed"] == 1

    @pytest.mark.asyncio
    async def test_pull_vessel_tracking_updates_state(
        self,
        mock_integration,
        mock_pull_config,
        mock_marine_monitor_client,
        mock_state_manager,
    ):
        """Test that stale subject handling (which manages state) is called after successful pull."""
        with patch_handler_dependencies(mock_marine_monitor_client, mock_state_manager) as mocks:
            await action_pull_vessel_tracking(
                integration=mock_integration,
                action_config=mock_pull_config,
            )

            mocks["handle_stale"].assert_called_once()

    @pytest.mark.asyncio
    async def test_pull_vessel_tracking_always_deactivates(
        self,
        mock_integration,
        mock_marine_monitor_client,
        mock_state_manager,
    ):
        """Test that stale subject deactivation always runs."""
        mock_config = MagicMock()
        mock_config.api_url = "https://test.example.com/api"
        mock_config.api_key.get_secret_value.return_value = "test-key"
        mock_config.minimal_confidence = 0.1
        mock_config.earthranger_subject_group_name = None

        with patch_handler_dependencies(
            mock_marine_monitor_client, mock_state_manager
        ) as mocks:
            await action_pull_vessel_tracking(
                integration=mock_integration,
                action_config=mock_config,
            )

            mocks["handle_stale"].assert_called_once()

    @pytest.mark.asyncio
    async def test_pull_vessel_tracking_filters_by_confidence(
        self,
        mock_integration,
        mock_state_manager,
    ):
        """Test that tracks below minimal confidence threshold are filtered out."""
        response = [
            {
                "id": 1,
                "name": "Test Station",
                "tracks": [
                    {
                        "id": 1,
                        "confidence": 0.8,
                        "track_detection": {
                            "timestamp": "2026-01-09T12:00:00Z",
                            "lat": 25.0,
                            "lon": -111.0,
                        },
                    },
                    {
                        "id": 2,
                        "confidence": 0.05,  # Below threshold
                        "track_detection": {
                            "timestamp": "2026-01-09T12:00:00Z",
                            "lat": 26.0,
                            "lon": -112.0,
                        },
                    },
                    {
                        "id": 3,
                        "confidence": 0.5,
                        "track_detection": {
                            "timestamp": "2026-01-09T12:00:00Z",
                            "lat": 27.0,
                            "lon": -113.0,
                        },
                    },
                ],
            },
        ]

        mock_client = create_mock_client(response)
        mock_config = MagicMock()
        mock_config.api_url = "https://test.example.com/api"
        mock_config.api_key.get_secret_value.return_value = "test-key"
        mock_config.minimal_confidence = 0.1
        mock_config.earthranger_subject_group_name = None

        with patch_handler_dependencies(mock_client, mock_state_manager) as mocks:
            result = await action_pull_vessel_tracking(
                integration=mock_integration,
                action_config=mock_config,
            )

            # Only 2 tracks should pass (confidence 0.8 and 0.5)
            # Track with confidence 0.05 should be filtered out
            assert result["observations_extracted"] == 2
            assert result["tracks_processed"] == 2

            assert mocks["er_client"].post_sensor_observation.call_count == 2
            payloads = [call[0][0] for call in mocks["er_client"].post_sensor_observation.call_args_list]
            manufacturer_ids = {p["manufacturer_id"] for p in payloads}
            assert "vessel-1" in manufacturer_ids
            assert "vessel-3" in manufacturer_ids
            assert "vessel-2" not in manufacturer_ids

    @pytest.mark.asyncio
    async def test_pull_vessel_tracking_includes_subject_group_in_payload(
        self,
        mock_integration,
        mock_marine_monitor_client,
        mock_state_manager,
    ):
        """Test that subject_groups is included in the payload when group name is configured."""
        mock_config = MagicMock()
        mock_config.api_url = "https://test.example.com/api"
        mock_config.api_key.get_secret_value.return_value = "test-key"
        mock_config.minimal_confidence = 0.1
        mock_config.earthranger_subject_group_name = "Marine Monitor"

        with patch_handler_dependencies(mock_marine_monitor_client, mock_state_manager) as mocks:
            await action_pull_vessel_tracking(
                integration=mock_integration,
                action_config=mock_config,
            )

            payload = mocks["er_client"].post_sensor_observation.call_args[0][0]
            assert payload["subject_groups"] == ["Marine Monitor"]

    @pytest.mark.asyncio
    async def test_pull_vessel_tracking_omits_subject_group_when_not_configured(
        self,
        mock_integration,
        mock_marine_monitor_client,
        mock_state_manager,
    ):
        """Test that subject_groups is not included in the payload when no group name is configured."""
        mock_config = MagicMock()
        mock_config.api_url = "https://test.example.com/api"
        mock_config.api_key.get_secret_value.return_value = "test-key"
        mock_config.minimal_confidence = 0.1
        mock_config.earthranger_subject_group_name = None

        with patch_handler_dependencies(mock_marine_monitor_client, mock_state_manager) as mocks:
            await action_pull_vessel_tracking(
                integration=mock_integration,
                action_config=mock_config,
            )

            payload = mocks["er_client"].post_sensor_observation.call_args[0][0]
            assert "subject_groups" not in payload
