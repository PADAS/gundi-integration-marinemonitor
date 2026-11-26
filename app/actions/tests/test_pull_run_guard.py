"""Tests for the pull action's run lock and its Gundi API retries."""
import pytest
import stamina
from unittest.mock import AsyncMock, patch

from gundi_client_v2.errors import GundiAPIError
from redis.exceptions import ConnectionError as RedisConnectionError

from app.actions.handlers import (
    PULL_LOCK_TTL_SECONDS,
    _RELEASE_PULL_LOCK_SCRIPT,
    _acquire_pull_lock,
    _pull_lock_key,
    _release_pull_lock,
    action_pull_vessel_tracking,
)
from app.actions.tests.conftest import patch_handler_dependencies
from app.services.activity_logger import ephemeral_run


@pytest.fixture(autouse=True)
def fast_retries():
    stamina.set_testing(True, attempts=3)
    yield
    stamina.set_testing(False)


class TestPullRunLock:

    @pytest.mark.asyncio
    async def test_run_is_skipped_while_another_holds_the_lock(
        self, mock_integration, mock_pull_config, mock_marine_monitor_client, mock_state_manager,
    ):
        mock_state_manager.db_client.set = AsyncMock(return_value=None)
        mock_state_manager.db_client.get = AsyncMock(return_value=b"another-runs-token")

        with patch_handler_dependencies(mock_marine_monitor_client, mock_state_manager) as mocks, patch(
            "app.actions.handlers.log_action_activity", new_callable=AsyncMock,
        ) as mock_log:
            result = await action_pull_vessel_tracking(
                integration=mock_integration, action_config=mock_pull_config,
            )

        assert result == {"skipped": "another run is still in progress"}
        mock_log.assert_awaited_once()
        mock_marine_monitor_client.get_track_markers.assert_not_called()
        mocks["er_client"].post_sensor_observation.assert_not_called()
        # A skipped run never touches the lock it doesn't own.
        mock_state_manager.db_client.eval.assert_not_called()

    @pytest.mark.asyncio
    async def test_lock_is_taken_and_released_with_the_same_token(
        self, mock_integration, mock_pull_config, mock_marine_monitor_client, mock_state_manager,
    ):
        key = _pull_lock_key(str(mock_integration.id))

        with patch_handler_dependencies(mock_marine_monitor_client, mock_state_manager):
            result = await action_pull_vessel_tracking(
                integration=mock_integration, action_config=mock_pull_config,
            )

        assert result["observations_extracted"] == 1
        set_args, set_kwargs = mock_state_manager.db_client.set.await_args
        assert set_args[0] == key
        assert set_kwargs == {"ex": PULL_LOCK_TTL_SECONDS, "nx": True}
        token = set_args[1]
        mock_state_manager.db_client.eval.assert_awaited_once_with(_RELEASE_PULL_LOCK_SCRIPT, 1, key, token)

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

        mock_state_manager.db_client.eval.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failed_lock_release_keeps_the_run_result(
        self, mock_integration, mock_pull_config, mock_marine_monitor_client, mock_state_manager,
    ):
        mock_state_manager.db_client.eval = AsyncMock(side_effect=RuntimeError("redis down"))

        with patch_handler_dependencies(mock_marine_monitor_client, mock_state_manager):
            result = await action_pull_vessel_tracking(
                integration=mock_integration, action_config=mock_pull_config,
            )

        assert result["observations_extracted"] == 1

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
        mock_state_manager.db_client.set.assert_not_called()


class TestPullLockOwnership:

    @pytest.mark.asyncio
    async def test_retried_acquire_after_lost_reply_still_owns_the_lock(self, mock_state_manager):
        # First SET succeeds on Redis but its reply is lost; the retry finds the key taken.
        taken = {}

        async def fake_set(key, value, ex, nx):
            if not taken:
                taken[key] = value
                raise RedisConnectionError("reply lost")
            return None

        async def fake_get(key):
            return taken[key].encode()

        mock_state_manager.db_client.set = AsyncMock(side_effect=fake_set)
        mock_state_manager.db_client.get = AsyncMock(side_effect=fake_get)

        token = await _acquire_pull_lock(mock_state_manager, "integration-1")

        assert token is not None
        assert token == taken[_pull_lock_key("integration-1")]

    @pytest.mark.asyncio
    async def test_release_only_deletes_the_lock_holding_this_runs_token(self, mock_state_manager):
        # Model the release script: delete only when the stored token matches.
        store = {_pull_lock_key("integration-1"): b"new-runs-token"}

        async def fake_eval(script, numkeys, key, token):
            if store.get(key) == token.encode():
                del store[key]
                return 1
            return 0

        mock_state_manager.db_client.eval = AsyncMock(side_effect=fake_eval)

        await _release_pull_lock(mock_state_manager, "integration-1", "old-runs-token")

        assert store == {_pull_lock_key("integration-1"): b"new-runs-token"}

    def test_release_script_compares_the_token_before_deleting(self):
        assert "redis.call('get', KEYS[1]) == ARGV[1]" in _RELEASE_PULL_LOCK_SCRIPT
        assert "redis.call('del', KEYS[1])" in _RELEASE_PULL_LOCK_SCRIPT


class TestGundiRetries:

    @pytest.mark.asyncio
    async def test_transient_gundi_error_is_retried(
        self, mock_integration, mock_pull_config, mock_marine_monitor_client, mock_state_manager,
    ):
        with patch_handler_dependencies(mock_marine_monitor_client, mock_state_manager) as mocks:
            connection = await mocks["gundi_client"].get_connection_details("x")
            mocks["gundi_client"].get_connection_details = AsyncMock(
                side_effect=[GundiAPIError(status_code=503), connection]
            )
            result = await action_pull_vessel_tracking(
                integration=mock_integration, action_config=mock_pull_config,
            )

        assert mocks["gundi_client"].get_connection_details.await_count == 2
        assert result["observations_extracted"] == 1

    @pytest.mark.asyncio
    async def test_gundi_client_error_is_not_retried(
        self, mock_integration, mock_pull_config, mock_marine_monitor_client, mock_state_manager,
    ):
        with patch_handler_dependencies(mock_marine_monitor_client, mock_state_manager) as mocks:
            mocks["gundi_client"].get_connection_details = AsyncMock(
                side_effect=GundiAPIError(status_code=404)
            )
            with pytest.raises(GundiAPIError):
                await action_pull_vessel_tracking(
                    integration=mock_integration, action_config=mock_pull_config,
                )

        assert mocks["gundi_client"].get_connection_details.await_count == 1
