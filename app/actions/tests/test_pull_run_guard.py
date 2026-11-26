"""Tests for the pull action's run lock and its Gundi API retries."""
import pytest
from unittest.mock import AsyncMock

from gundi_client_v2.errors import GundiAPIError

from app.actions.handlers import (
    PULL_ACTION_ID,
    PULL_LOCK_STATE_ID,
    PULL_LOCK_TTL_SECONDS,
    action_pull_vessel_tracking,
)
from app.actions.tests.conftest import patch_handler_dependencies
from app.services.activity_logger import ephemeral_run


class TestPullRunLock:

    @pytest.mark.asyncio
    async def test_run_is_skipped_while_another_holds_the_lock(
        self, mock_integration, mock_pull_config, mock_marine_monitor_client, mock_state_manager,
    ):
        mock_state_manager.set_if_absent = AsyncMock(return_value=False)

        with patch_handler_dependencies(mock_marine_monitor_client, mock_state_manager) as mocks:
            result = await action_pull_vessel_tracking(
                integration=mock_integration, action_config=mock_pull_config,
            )

        assert result == {"skipped": "another run is still in progress"}
        mock_marine_monitor_client.get_track_markers.assert_not_called()
        mocks["er_client"].post_sensor_observation.assert_not_called()
        # The lock belongs to the run that holds it: a skipped run never releases it.
        mock_state_manager.delete_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_lock_is_taken_and_released_around_a_run(
        self, mock_integration, mock_pull_config, mock_marine_monitor_client, mock_state_manager,
    ):
        integration_id = str(mock_integration.id)

        with patch_handler_dependencies(mock_marine_monitor_client, mock_state_manager):
            result = await action_pull_vessel_tracking(
                integration=mock_integration, action_config=mock_pull_config,
            )

        assert result["observations_extracted"] == 1
        mock_state_manager.set_if_absent.assert_awaited_once_with(
            integration_id, PULL_ACTION_ID, ttl_seconds=PULL_LOCK_TTL_SECONDS, source_id=PULL_LOCK_STATE_ID,
        )
        mock_state_manager.delete_state.assert_any_await(
            integration_id, PULL_ACTION_ID, source_id=PULL_LOCK_STATE_ID,
        )

    @pytest.mark.asyncio
    async def test_lock_is_released_when_the_run_fails(
        self, mock_integration, mock_pull_config, mock_marine_monitor_client, mock_state_manager,
    ):
        mock_marine_monitor_client.get_track_markers = AsyncMock(side_effect=RuntimeError("boom"))

        with patch_handler_dependencies(mock_marine_monitor_client, mock_state_manager):
            with pytest.raises(RuntimeError):
                await action_pull_vessel_tracking(
                    integration=mock_integration, action_config=mock_pull_config,
                )

        mock_state_manager.delete_state.assert_any_await(
            str(mock_integration.id), PULL_ACTION_ID, source_id=PULL_LOCK_STATE_ID,
        )

    @pytest.mark.asyncio
    async def test_ephemeral_run_needs_no_lock(
        self, mock_integration, mock_pull_config, mock_marine_monitor_client, mock_state_manager,
    ):
        token = ephemeral_run.set(True)
        try:
            with patch_handler_dependencies(mock_marine_monitor_client, mock_state_manager):
                result = await action_pull_vessel_tracking(
                    integration=mock_integration, action_config=mock_pull_config,
                )
        finally:
            ephemeral_run.reset(token)

        assert result["observations_extracted"] == 1
        mock_state_manager.set_if_absent.assert_not_called()


class TestGundiRetries:

    @pytest.mark.asyncio
    async def test_transient_gundi_error_is_retried(
        self, mock_integration, mock_pull_config, mock_marine_monitor_client, mock_state_manager,
    ):
        import stamina
        stamina.set_testing(True, attempts=3)
        try:
            with patch_handler_dependencies(mock_marine_monitor_client, mock_state_manager) as mocks:
                connection = await mocks["gundi_client"].get_connection_details("x")
                mocks["gundi_client"].get_connection_details = AsyncMock(
                    side_effect=[GundiAPIError(status_code=503), connection]
                )
                result = await action_pull_vessel_tracking(
                    integration=mock_integration, action_config=mock_pull_config,
                )
        finally:
            stamina.set_testing(False)

        assert mocks["gundi_client"].get_connection_details.await_count == 2
        assert result["observations_extracted"] == 1

    @pytest.mark.asyncio
    async def test_gundi_client_error_is_not_retried(
        self, mock_integration, mock_pull_config, mock_marine_monitor_client, mock_state_manager,
    ):
        import stamina
        stamina.set_testing(True, attempts=3)
        try:
            with patch_handler_dependencies(mock_marine_monitor_client, mock_state_manager) as mocks:
                mocks["gundi_client"].get_connection_details = AsyncMock(
                    side_effect=GundiAPIError(status_code=404)
                )
                with pytest.raises(GundiAPIError):
                    await action_pull_vessel_tracking(
                        integration=mock_integration, action_config=mock_pull_config,
                    )
        finally:
            stamina.set_testing(False)

        assert mocks["gundi_client"].get_connection_details.await_count == 1
