"""Tests for Marine Monitor action handlers."""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from erclient.er_errors import ERClientNotFound, ERClientBadCredentials, ERClientPermissionDenied

from app.actions.handlers import (
    transform_track_to_observation,
    parse_timestamp,
    action_pull_vessel_tracking,
    delete_vessel_from_earthranger,
    action_view_cached_vessel_data,
    action_reset_cached_vessel_data,
    _process_track,
    _get_stale_vessel_ids,
    _delete_stale_vessels_from_er,
    _update_vessel_state,
    _is_permanent_er_error,
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


class TestDeleteVesselFromEarthRanger:
    """Tests for delete_vessel_from_earthranger function."""

    @pytest.mark.asyncio
    async def test_delete_vessel_success(self):
        """Test successful subject and source deletion."""
        mock_client = MagicMock()
        mock_client.get_source_by_manufacturer_id = AsyncMock(
            return_value={"data": {"id": "source-uuid-123", "manufacturer_id": "48590736"}}
        )
        mock_client.get_source_subjects = AsyncMock(
            return_value=[
                {
                    "id": "subject-uuid",
                    "last_position_date": "2026-01-09T12:00:00Z",
                }
            ]
        )
        mock_client.delete_subject = AsyncMock(return_value=None)
        mock_client.delete_source = AsyncMock(return_value=None)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("app.actions.handlers.AsyncERClient", return_value=mock_client):
            result = await delete_vessel_from_earthranger(
                track_id="48590736",
                er_base_url="https://test.pamdas.org",
                er_token="test-token",
            )

        assert result is True
        mock_client.delete_subject.assert_called_once_with("subject-uuid")
        mock_client.delete_source.assert_called_once_with("source-uuid-123", async_mode=True)


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
        """Test that stale vessel removal (which manages state) is called after successful pull."""
        with patch_handler_dependencies(mock_marine_monitor_client, mock_state_manager) as mocks:
            await action_pull_vessel_tracking(
                integration=mock_integration,
                action_config=mock_pull_config,
            )

            mocks["remove_stale"].assert_called_once()

    @pytest.mark.asyncio
    async def test_pull_vessel_tracking_always_deactivates(
        self,
        mock_integration,
        mock_marine_monitor_client,
        mock_state_manager,
    ):
        """Test that stale vessel removal always runs."""
        mock_config = MagicMock()
        mock_config.api_url = "https://test.example.com/api"
        mock_config.api_key.get_secret_value.return_value = "test-key"
        mock_config.minimal_confidence = 0.1
        mock_config.earthranger_subject_group_name = None
        mock_config.earthranger_subject_subtype_id = None

        with patch_handler_dependencies(
            mock_marine_monitor_client, mock_state_manager
        ) as mocks:
            await action_pull_vessel_tracking(
                integration=mock_integration,
                action_config=mock_config,
            )

            mocks["remove_stale"].assert_called_once()

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
        mock_config.earthranger_subject_subtype_id = None

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
        mock_config.earthranger_subject_subtype_id = None

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
        mock_config.earthranger_subject_subtype_id = None

        with patch_handler_dependencies(mock_marine_monitor_client, mock_state_manager) as mocks:
            await action_pull_vessel_tracking(
                integration=mock_integration,
                action_config=mock_config,
            )

            payload = mocks["er_client"].post_sensor_observation.call_args[0][0]
            assert "subject_groups" not in payload
            assert "subject_subtype_id" not in payload


class TestActionViewCachedVesselData:

    @pytest.mark.asyncio
    async def test_returns_known_vessels(self, mock_integration, mock_state_manager):
        """Returns vessel list from Redis state."""
        mock_state_manager.get_state = AsyncMock(side_effect=[
            {"track_ids": ["vessel-111", "vessel-222"], "last_run": "2026-03-18T10:00:00Z"},  # known_vessels
            {"last_seen": "2026-03-18T10:00:00Z"},  # vessel-111
            {"last_seen": "2026-03-18T09:00:00Z"},  # vessel-222
        ])
        config = MagicMock()

        with patch("app.actions.handlers.IntegrationStateManager", return_value=mock_state_manager), \
             patch("app.services.activity_logger.publish_event", new_callable=AsyncMock):
            result = await action_view_cached_vessel_data(integration=mock_integration, action_config=config)

        assert result["total"] == 2
        assert result["last_updated"] == "2026-03-18T10:00:00Z"
        assert result["known_vessels"][0]["track_id"] == "vessel-111"
        assert result["known_vessels"][0]["last_seen"] == "2026-03-18T10:00:00Z"
        assert result["known_vessels"][1]["track_id"] == "vessel-222"

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_state(self, mock_integration, mock_state_manager):
        """Returns empty list when Redis has no known_vessels state."""
        mock_state_manager.get_state = AsyncMock(return_value={})
        config = MagicMock()

        with patch("app.actions.handlers.IntegrationStateManager", return_value=mock_state_manager), \
             patch("app.services.activity_logger.publish_event", new_callable=AsyncMock):
            result = await action_view_cached_vessel_data(integration=mock_integration, action_config=config)

        assert result["total"] == 0
        assert result["known_vessels"] == []
        assert result["last_updated"] is None



class TestActionResetCachedVesselData:

    @pytest.mark.asyncio
    async def test_clears_all_vessel_state(self, mock_integration, mock_state_manager):
        """Deletes all per-vessel keys and known_vessels index from Redis."""
        mock_state_manager.get_state = AsyncMock(return_value={
            "track_ids": ["vessel-111", "vessel-222"],
            "last_run": "2026-03-18T10:00:00Z",
        })
        config = MagicMock()

        with patch("app.actions.handlers.IntegrationStateManager", return_value=mock_state_manager), \
             patch("app.services.activity_logger.publish_event", new_callable=AsyncMock):
            result = await action_reset_cached_vessel_data(integration=mock_integration, action_config=config)

        assert result["vessels_cleared"] == 2
        assert mock_state_manager.delete_state.call_count == 3  # 2 vessels + known_vessels index

    @pytest.mark.asyncio
    async def test_clears_empty_state(self, mock_integration, mock_state_manager):
        """Handles empty Redis state gracefully."""
        mock_state_manager.get_state = AsyncMock(return_value={})
        config = MagicMock()

        with patch("app.actions.handlers.IntegrationStateManager", return_value=mock_state_manager), \
             patch("app.services.activity_logger.publish_event", new_callable=AsyncMock):
            result = await action_reset_cached_vessel_data(integration=mock_integration, action_config=config)

        assert result["vessels_cleared"] == 0
        assert mock_state_manager.delete_state.call_count == 1  # only known_vessels index


class TestIsPermanentErError:

    def test_bad_credentials_is_permanent(self):
        assert _is_permanent_er_error(ERClientBadCredentials("msg")) is True

    def test_permission_denied_is_permanent(self):
        assert _is_permanent_er_error(ERClientPermissionDenied("msg")) is True

    def test_not_found_is_not_permanent(self):
        assert _is_permanent_er_error(ERClientNotFound("msg")) is False

    def test_generic_exception_is_not_permanent(self):
        assert _is_permanent_er_error(Exception("generic")) is False


class TestDeleteVesselFromEarthRangerEdgeCases:

    @pytest.mark.asyncio
    async def test_source_not_found_returns_false(self):
        """Returns False when manufacturer_id has no source in ER."""
        mock_client = MagicMock()
        mock_client.get_source_by_manufacturer_id = AsyncMock(
            side_effect=ERClientNotFound("not found")
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("app.actions.handlers.AsyncERClient", return_value=mock_client):
            result = await delete_vessel_from_earthranger(
                track_id="vessel-999",
                er_base_url="https://test.pamdas.org",
                er_token="test-token",
            )

        assert result is False
        mock_client.delete_subject.assert_not_called()
        mock_client.delete_source.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_subjects_returns_false(self):
        """Returns False when source has no linked subjects."""
        mock_client = MagicMock()
        mock_client.get_source_by_manufacturer_id = AsyncMock(
            return_value={"data": {"id": "source-uuid"}}
        )
        mock_client.get_source_subjects = AsyncMock(return_value=[])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("app.actions.handlers.AsyncERClient", return_value=mock_client):
            result = await delete_vessel_from_earthranger(
                track_id="vessel-999",
                er_base_url="https://test.pamdas.org",
                er_token="test-token",
            )

        assert result is False
        mock_client.delete_subject.assert_not_called()
        mock_client.delete_source.assert_not_called()

    @pytest.mark.asyncio
    async def test_subject_missing_id_returns_false(self):
        """Returns False when subject response has no 'id' field."""
        mock_client = MagicMock()
        mock_client.get_source_by_manufacturer_id = AsyncMock(
            return_value={"data": {"id": "source-uuid"}}
        )
        mock_client.get_source_subjects = AsyncMock(return_value=[{"last_position_date": "2026-01-01T00:00:00Z"}])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("app.actions.handlers.AsyncERClient", return_value=mock_client):
            result = await delete_vessel_from_earthranger(
                track_id="vessel-999",
                er_base_url="https://test.pamdas.org",
                er_token="test-token",
            )

        assert result is False
        mock_client.delete_subject.assert_not_called()


class TestProcessTrackEdgeCases:

    def test_no_timestamp_returns_none(self, sample_radar_station):
        """Track with no timestamp (neither in track_detection nor last_update) is skipped."""
        track = {"id": 1, "confidence": 0.8}
        assert _process_track(track, sample_radar_station) is None

    def test_invalid_timestamp_returns_none(self, sample_radar_station):
        """Track with unparseable timestamp is skipped."""
        track = {"id": 1, "confidence": 0.8, "last_update": "not-a-valid-date"}
        assert _process_track(track, sample_radar_station) is None

    def test_confidence_exactly_at_threshold_passes(self, sample_radar_station):
        """Track with confidence equal to threshold is not filtered out."""
        track = {
            "id": 1,
            "confidence": 0.5,
            "last_update": "2026-01-09T12:00:00Z",
            "track_detection": {"lat": 25.0, "lon": -111.0},
        }
        assert _process_track(track, sample_radar_station, minimal_confidence=0.5) is not None


class TestGetStaleVesselIds:

    @pytest.mark.asyncio
    async def test_returns_vessels_not_in_active_set(self, mock_state_manager):
        mock_state_manager.get_state = AsyncMock(return_value={
            "track_ids": ["vessel-111", "vessel-222", "vessel-333"],
        })
        result = await _get_stale_vessel_ids(mock_state_manager, "integration-id", {"vessel-111"})
        assert result == {"vessel-222", "vessel-333"}

    @pytest.mark.asyncio
    async def test_returns_empty_when_all_active(self, mock_state_manager):
        mock_state_manager.get_state = AsyncMock(return_value={
            "track_ids": ["vessel-111"],
        })
        result = await _get_stale_vessel_ids(mock_state_manager, "integration-id", {"vessel-111"})
        assert result == set()

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_known_state(self, mock_state_manager):
        mock_state_manager.get_state = AsyncMock(return_value={})
        result = await _get_stale_vessel_ids(mock_state_manager, "integration-id", {"vessel-111"})
        assert result == set()


class TestDeleteStaleVesselsFromEr:

    @pytest.mark.asyncio
    async def test_returns_all_successfully_deleted_ids(self):
        with patch("app.actions.handlers.delete_vessel_from_earthranger", new_callable=AsyncMock, return_value=True):
            result = await _delete_stale_vessels_from_er(
                stale_vessel_ids={"vessel-111", "vessel-222"},
                er_base_url="https://test.pamdas.org",
                er_token="test-token",
            )
        assert set(result) == {"vessel-111", "vessel-222"}

    @pytest.mark.asyncio
    async def test_partial_deletion_returns_only_successful(self):
        async def mock_delete(track_id, **kwargs):
            return track_id == "vessel-111"

        with patch("app.actions.handlers.delete_vessel_from_earthranger", side_effect=mock_delete):
            result = await _delete_stale_vessels_from_er(
                stale_vessel_ids={"vessel-111", "vessel-222"},
                er_base_url="https://test.pamdas.org",
                er_token="test-token",
            )
        assert result == ["vessel-111"]

    @pytest.mark.asyncio
    async def test_empty_stale_set_returns_empty_without_calling_delete(self):
        with patch("app.actions.handlers.delete_vessel_from_earthranger", new_callable=AsyncMock) as mock_delete:
            result = await _delete_stale_vessels_from_er(
                stale_vessel_ids=set(),
                er_base_url="https://test.pamdas.org",
                er_token="test-token",
            )
        assert result == []
        mock_delete.assert_not_called()


class TestUpdateVesselState:

    @pytest.mark.asyncio
    async def test_deletes_per_vessel_key_for_each_deleted_vessel(self, mock_state_manager):
        now = datetime(2026, 3, 23, 10, 0, 0, tzinfo=timezone.utc)
        mock_state_manager.get_state = AsyncMock(return_value={})

        await _update_vessel_state(
            state_manager=mock_state_manager,
            integration_id="integration-id",
            active_track_ids=set(),
            deleted_track_ids=["vessel-222", "vessel-333"],
            now=now,
        )

        deleted_source_ids = {
            call.kwargs["source_id"]
            for call in mock_state_manager.delete_state.call_args_list
        }
        assert deleted_source_ids == {"vessel-222", "vessel-333"}

    @pytest.mark.asyncio
    async def test_updates_known_vessels_index(self, mock_state_manager):
        now = datetime(2026, 3, 23, 10, 0, 0, tzinfo=timezone.utc)
        mock_state_manager.get_state = AsyncMock(return_value={})

        await _update_vessel_state(
            state_manager=mock_state_manager,
            integration_id="integration-id",
            active_track_ids={"vessel-111"},
            deleted_track_ids=[],
            now=now,
        )

        known_vessels_call = mock_state_manager.set_state.call_args_list[0]
        assert known_vessels_call.kwargs["source_id"] == "known_vessels"
        assert "vessel-111" in known_vessels_call.kwargs["state"]["track_ids"]
        assert known_vessels_call.kwargs["state"]["last_run"] == now.isoformat()

    @pytest.mark.asyncio
    async def test_refreshes_last_seen_and_preserves_existing_state(self, mock_state_manager):
        now = datetime(2026, 3, 23, 10, 0, 0, tzinfo=timezone.utc)
        mock_state_manager.get_state = AsyncMock(return_value={"custom_field": "value"})

        await _update_vessel_state(
            state_manager=mock_state_manager,
            integration_id="integration-id",
            active_track_ids={"vessel-111"},
            deleted_track_ids=[],
            now=now,
        )

        vessel_call = mock_state_manager.set_state.call_args_list[1]
        assert vessel_call.kwargs["source_id"] == "vessel-111"
        assert vessel_call.kwargs["state"]["last_seen"] == now.isoformat()
        assert vessel_call.kwargs["state"]["custom_field"] == "value"


class TestActionPullVesselTrackingDestinationValidation:

    @pytest.mark.asyncio
    async def test_skips_destination_without_auth_config(
        self,
        mock_integration,
        mock_pull_config,
        mock_marine_monitor_client,
        mock_state_manager,
    ):
        """Destination with no auth config is skipped — no ER calls made."""
        mock_dest_integration = MagicMock()
        mock_dest_integration.get_action_config = MagicMock(return_value=None)

        with patch_handler_dependencies(
            mock_marine_monitor_client, mock_state_manager,
            mock_dest_integration=mock_dest_integration,
        ) as mocks:
            await action_pull_vessel_tracking(
                integration=mock_integration,
                action_config=mock_pull_config,
            )

        mocks["er_client"].post_sensor_observation.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_destination_without_token(
        self,
        mock_integration,
        mock_pull_config,
        mock_marine_monitor_client,
        mock_state_manager,
    ):
        """Destination with missing token is skipped — no ER calls made."""
        mock_dest_integration = MagicMock()
        auth_config = MagicMock()
        auth_config.data = {"token": None}
        mock_dest_integration.get_action_config = MagicMock(return_value=auth_config)

        with patch_handler_dependencies(
            mock_marine_monitor_client, mock_state_manager,
            mock_dest_integration=mock_dest_integration,
        ) as mocks:
            await action_pull_vessel_tracking(
                integration=mock_integration,
                action_config=mock_pull_config,
            )

        mocks["er_client"].post_sensor_observation.assert_not_called()

    @pytest.mark.asyncio
    async def test_includes_subject_subtype_id_in_payload(
        self,
        mock_integration,
        mock_marine_monitor_client,
        mock_state_manager,
    ):
        """subject_subtype_id is included in the ER payload when configured."""
        mock_config = MagicMock()
        mock_config.api_url = "https://test.example.com/api"
        mock_config.api_key.get_secret_value.return_value = "test-key"
        mock_config.minimal_confidence = 0.1
        mock_config.earthranger_subject_group_name = None
        mock_config.earthranger_subject_subtype_id = "vessel"

        with patch_handler_dependencies(mock_marine_monitor_client, mock_state_manager) as mocks:
            await action_pull_vessel_tracking(
                integration=mock_integration,
                action_config=mock_config,
            )

        payload = mocks["er_client"].post_sensor_observation.call_args[0][0]
        assert payload["subject_subtype_id"] == "vessel"
