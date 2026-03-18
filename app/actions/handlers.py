"""Action handlers for Marine Monitor integration."""
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import backoff
import httpx
import stamina
from erclient import AsyncERClient
from erclient.er_errors import ERClientNotFound, ERClientPermissionDenied, ERClientBadCredentials
from gundi_client_v2 import GundiClient

from app.actions.configurations import (
    PullVesselTrackingConfiguration,
    GetVesselStateConfiguration,
    DeleteVesselConfiguration,
    ClearVesselStateConfiguration,
)
from app.actions.marine_monitor import MarineMonitorClient
from app.services.action_scheduler import crontab_schedule
from app.services.activity_logger import activity_logger, log_action_activity
from app.services.state import IntegrationStateManager

logger = logging.getLogger(__name__)


def get_position_date(subject):
    date_str = subject.get("last_position_date")
    if not date_str:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return parse_timestamp(date_str)
    except (ValueError, TypeError):
        return datetime.min.replace(tzinfo=timezone.utc)


async def delete_vessel_from_earthranger(
    track_id: str,
    er_base_url: str,
    er_token: str,
    subject_id: str | None = None,
) -> bool:
    """Delete a stale vessel subject and its source from EarthRanger.

    Always looks up the source by manufacturer_id (needed for source deletion).
    If subject_id is provided, skips the subjects lookup.

    WARNING: Irreversible — deleted subjects and sources lose all observation history.

    1. Gets the source by manufacturer_id (track_id)
    2. If no subject_id: gets all subjects linked to the source, picks most recent
    3. Deletes the subject
    4. Deletes the source

    :param track_id: The vessel ID used as manufacturer_id in ER
    :param er_base_url: The EarthRanger base URL
    :param er_token: The EarthRanger auth token
    :param subject_id: Optional cached subject UUID — skips subjects lookup if provided
    :return: True if deletion was successful, False otherwise
    """
    try:
        async with AsyncERClient(
            service_root=f"{er_base_url}/api/v1.0",
            token=er_token,
        ) as client:
            # Always look up source — needed for source deletion
            try:
                source_response = await client.get_source_by_manufacturer_id(track_id)
                source = source_response.get("data", source_response)
            except ERClientNotFound:
                logger.warning(
                    f"Source not found for track_id '{track_id}', skipping deletion"
                )
                return False

            source_id = source.get("id")
            if not source_id:
                logger.warning(f"Source response missing 'id' for track_id '{track_id}'")
                return False

            if not subject_id:
                subjects = await client.get_source_subjects(source_id)
                if not subjects:
                    logger.info(f"No subjects found for vessel '{track_id}', skipping deletion")
                    return False

                subject_to_delete = max(subjects, key=get_position_date)
                subject_id = subject_to_delete.get("id")

                if not subject_id:
                    logger.warning("Subject missing 'id' field")
                    return False

            await client.delete_subject(subject_id)
            await client.delete_source(source_id, async_mode=True)
            logger.info(f"Requested deletion of vessel '{track_id}' from EarthRanger (subject: {subject_id}, source: {source_id})")
            return True

    except Exception as e:
        logger.exception(f"Failed to delete subject/source for track_id '{track_id}': {e}")
        return False


def _add_optional_field(
    additional: dict[str, Any],
    key: str,
    value: Any,
    transform: type | None = None,
) -> None:
    """Add a field to additional dict if value is not None."""
    if value is not None:
        additional[key] = transform(value) if transform else value


def transform_track_to_observation(
    track: dict[str, Any],
    radar_station: dict[str, Any],
) -> dict[str, Any]:
    """Transform a Marine Monitor track to Gundi observation format.

    :param track: Track data from Marine Monitor API
    :param radar_station: Radar station data containing metadata
    :return: Observation in Gundi schema format
    """
    track_detection = track.get("track_detection", {})
    track_id = track.get("id", "unknown-source")

    # Use track ID as source identifier, fallback to default if not set
    source_id = f"vessel-{track_id}"
    vessel_name = track.get("vessel_name")
    subject_name = f"{source_id} ({vessel_name})" if vessel_name else source_id

    # Prefer track_detection timestamp, fallback to last_update
    timestamp = track_detection.get("timestamp") or track.get("last_update")

    additional: dict[str, Any] = {}

    # Track detection fields
    _add_optional_field(additional, "speed_kmph", track_detection.get("speed"), float)
    _add_optional_field(additional, "heading", track_detection.get("heading"), float)
    _add_optional_field(additional, "bearing", track_detection.get("bearing"), float)
    _add_optional_field(additional, "distance_nm", track_detection.get("distance"), float)

    # Track metadata
    _add_optional_field(additional, "confidence", track.get("confidence"), float)
    _add_optional_field(additional, "radar_track_id", track.get("radar_track_id"))
    _add_optional_field(additional, "vessel_name", track.get("vessel_name"))
    _add_optional_field(additional, "track_source", track.get("source"))
    _add_optional_field(additional, "track_started", track.get("started"))
    _add_optional_field(additional, "active", track.get("active"), bool)

    # Radar station metadata
    _add_optional_field(additional, "radar_station_name", radar_station.get("name"))
    _add_optional_field(additional, "radar_station_id", radar_station.get("id"))

    return {
        "source": source_id,
        "subject_name": subject_name,
        "type": "tracking-device",
        "subject_type": "vehicle",
        "recorded_at": timestamp,
        "location": {
            "lat": float(track_detection.get("lat", 0.0)),
            "lon": float(track_detection.get("lon", 0.0)),
        },
        "additional": additional,
    }


def parse_timestamp(timestamp_str: str) -> datetime:
    """Parse ISO format timestamp string to datetime.

    :param timestamp_str: ISO format timestamp (may end with 'Z' or have timezone)
    :return: Timezone-aware datetime in UTC
    """
    if timestamp_str.endswith("Z"):
        timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    else:
        timestamp = datetime.fromisoformat(timestamp_str)

    # Convert to UTC if timezone-aware, otherwise assume UTC
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    else:
        timestamp = timestamp.astimezone(timezone.utc)

    return timestamp


def _process_track(
    track: dict[str, Any],
    radar_station: dict[str, Any],
    minimal_confidence: float = 0.1,
) -> dict[str, Any] | None:
    """Process a single track and return observation if valid.

    Returns None if the track has no valid timestamp or confidence is below threshold.
    """
    # Check confidence threshold
    confidence = track.get("confidence")
    if confidence is not None and confidence < minimal_confidence:
        logger.debug(
            f"Track {track.get('id')} confidence {confidence} is below "
            f"threshold {minimal_confidence}, skipping"
        )
        return None

    track_detection = track.get("track_detection", {})
    timestamp_str = track_detection.get("timestamp") or track.get("last_update")

    if not timestamp_str:
        logger.debug(f"Track {track.get('id')} has no timestamp, skipping")
        return None

    try:
        parse_timestamp(timestamp_str)  # Validate timestamp is parseable
    except (ValueError, TypeError) as e:
        logger.warning(f"Failed to parse timestamp '{timestamp_str}': {e}")
        return None

    return transform_track_to_observation(track, radar_station)


async def _remove_stale_vessels(
    state_manager: IntegrationStateManager,
    integration_id: str,
    er_base_url: str,
    er_token: str,
    active_track_ids: set[str],
    now: datetime,
) -> list[str]:
    """Delete stale vessels from EarthRanger and update Redis state.

    Compares currently active vessels with the known vessels list stored in Redis.
    Deletes vessels from EarthRanger that are no longer appearing in the API.

    Returns the list of deleted track_ids.
    """
    deleted_track_ids: list[str] = []

    # Load known vessels list from Redis to find which vessels we saw last run
    # ToDo: Use state manager Groups (redis set) as in the ATS integration, once available in the template repo
    known_vessels_state = await state_manager.get_state(
        integration_id=integration_id,
        action_id="pull_vessel_tracking",
        source_id="known_vessels",
    )
    known_vessel_ids = set(known_vessels_state.get("track_ids", []))

    # Vessels missing this run = last run list minus current run list
    stale_vessel_ids = known_vessel_ids - active_track_ids

    for track_id in stale_vessel_ids:
        logger.info(f"Vessel '{track_id}' is stale, removing from EarthRanger")

        vessel_state = await state_manager.get_state(
            integration_id=integration_id,
            action_id="pull_vessel_tracking",
            source_id=track_id,
        )
        deleted = await delete_vessel_from_earthranger(
            track_id=track_id,
            er_base_url=er_base_url,
            er_token=er_token,
            subject_id=vessel_state.get("subject_id"),
        )

        if deleted:
            deleted_track_ids.append(track_id)
            await state_manager.delete_state(
                integration_id=integration_id,
                action_id="pull_vessel_tracking",
                source_id=track_id,
            )
            logger.info(f"Requested deletion of vessel '{track_id}' from EarthRanger and removed from Redis")

    # Save updated known vessels list to Redis with current timestamp
    await state_manager.set_state(
        integration_id=integration_id,
        action_id="pull_vessel_tracking",
        state={"track_ids": list(active_track_ids), "last_run": now.isoformat()},
        source_id="known_vessels",
    )

    # Store individual state for each active vessel, preserving existing fields (e.g. subject_id)
    for track_id in active_track_ids:
        existing = await state_manager.get_state(
            integration_id=integration_id,
            action_id="pull_vessel_tracking",
            source_id=track_id,
        )
        await state_manager.set_state(
            integration_id=integration_id,
            action_id="pull_vessel_tracking",
            state={**existing, "last_seen": now.isoformat()},
            source_id=track_id,
        )

    return deleted_track_ids



def _is_permanent_er_error(e: Exception) -> bool:
    return isinstance(e, (ERClientBadCredentials, ERClientPermissionDenied))


@backoff.on_exception(backoff.expo, Exception, max_tries=5, jitter=backoff.full_jitter, giveup=_is_permanent_er_error)
async def _post_observation_to_er(client: AsyncERClient, observation: dict, subject_group_name: Optional[str] = None) -> None:
    payload = {
        "manufacturer_id": observation["source"],
        "subject_name": observation["subject_name"],
        "recorded_at": observation["recorded_at"],
        "location": observation["location"],
        "additional": observation.get("additional", {}),
    }
    if subject_group_name:
        payload["subject_groups"] = [subject_group_name]
    await client.post_sensor_observation(payload)


@crontab_schedule("*/5 * * * *")  # Run every 5 minutes
@activity_logger()
async def action_pull_vessel_tracking(
    integration,
    action_config: PullVesselTrackingConfiguration,
) -> dict[str, int]:
    """Pull vessel tracking data from Marine Monitor API and send to Gundi.

    Fetches radar station data with vessel tracks and transforms them to
    Gundi observations. Uses state management to track which observations
    have been processed. Optionally deletes stale vessels from EarthRanger.
    """
    state_manager = IntegrationStateManager()
    integration_id = str(integration.id)

    results = {
        "observations_extracted": 0,
        "radar_stations_processed": 0,
        "tracks_processed": 0,
        "vessels_deleted": 0,
        "radar_stations_failed": 0,
    }

    async with MarineMonitorClient(
        api_url=action_config.api_url,
        api_key=action_config.api_key.get_secret_value(),
    ) as client:
        radar_stations = await client.get_track_markers()
        now = datetime.now(timezone.utc)
        all_observations: list[dict[str, Any]] = []
        active_track_ids: set[str] = set()

        for radar_station in radar_stations:
            radar_id = radar_station.get("id")
            if not radar_id:
                logger.warning("Skipping radar station without ID")
                continue

            try:
                for track in radar_station.get("tracks", []):
                    observation = _process_track(
                        track, radar_station, action_config.minimal_confidence
                    )
                    if observation:
                        all_observations.append(observation)
                        active_track_ids.add(observation["source"])
                        results["tracks_processed"] += 1

                results["radar_stations_processed"] += 1

            except Exception as e:
                logger.exception(f"Failed to process radar station {radar_id}: {e}")
                results["radar_stations_failed"] += 1

        async with GundiClient() as gundi:
            async for attempt in stamina.retry_context(on=httpx.HTTPError, wait_initial=1.0, wait_jitter=5.0, wait_max=32.0):
                with attempt:
                    connection = await gundi.get_connection_details(integration_id)
        if not connection.destinations:
            logger.warning(f"No destinations configured for integration '{integration_id}', skipping vessel deletion and group assignment")
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

            if all_observations:
                logger.info(f"Sending {len(all_observations)} vessel observations to EarthRanger at {dest_base_url}")
                await log_action_activity(
                    integration_id=integration_id,
                    action_id="pull_vessel_tracking",
                    title=f"Sending {len(all_observations)} vessel observations to EarthRanger",
                    level=logging.INFO,
                    data={"vessels": [o["subject_name"] for o in all_observations]},
                )
                async with AsyncERClient(
                    service_root=f"{dest_base_url}/api/v1.0",
                    token=dest_token,
                    provider_key=f"gundi_marine_monitor_{integration_id}",
                ) as er_client:
                    for observation in all_observations:
                        await _post_observation_to_er(er_client, observation, action_config.earthranger_subject_group_name)
                results["observations_extracted"] = len(all_observations)
                logger.info(f"Sent {len(all_observations)} observations to EarthRanger at {dest_base_url}")

            deleted_track_ids = await _remove_stale_vessels(
                state_manager=state_manager,
                integration_id=integration_id,
                er_base_url=dest_base_url,
                er_token=dest_token,
                active_track_ids=active_track_ids,
                now=now,
            )
            if deleted_track_ids:
                await log_action_activity(
                    integration_id=integration_id,
                    action_id="pull_vessel_tracking",
                    title=f"Removed {len(deleted_track_ids)} stale vessels from EarthRanger",
                    level=logging.INFO,
                    data={"removed_vessels": deleted_track_ids},
                )
            results["vessels_deleted"] += len(deleted_track_ids)

    updated_vessels = list({o["subject_name"] for o in all_observations})
    await log_action_activity(
        integration_id=integration_id,
        action_id="pull_vessel_tracking",
        title=f"Finished: {results['observations_extracted']} observations sent, {results['vessels_deleted']} stale vessels removed",
        level=logging.INFO,
        data={"updated_vessels": updated_vessels},
    )

    return results


@activity_logger()
async def action_get_vessel_state(
    integration,
    action_config: GetVesselStateConfiguration,
) -> dict:
    state_manager = IntegrationStateManager()
    integration_id = str(integration.id)

    known_vessels_state = await state_manager.get_state(
        integration_id=integration_id,
        action_id="pull_vessel_tracking",
        source_id="known_vessels",
    )
    track_ids = known_vessels_state.get("track_ids", [])

    vessels = []
    for track_id in track_ids:
        vessel_state = await state_manager.get_state(
            integration_id=integration_id,
            action_id="pull_vessel_tracking",
            source_id=track_id,
        )
        vessels.append({
            "track_id": track_id,
            "subject_id": vessel_state.get("subject_id"),
            "last_seen": vessel_state.get("last_seen"),
        })

    return {
        "total": len(vessels),
        "last_updated": known_vessels_state.get("last_run"),
        "known_vessels": vessels,
    }


@activity_logger()
async def action_delete_vessel(
    integration,
    action_config: DeleteVesselConfiguration,
) -> dict:
    state_manager = IntegrationStateManager()
    integration_id = str(integration.id)
    track_id = f"vessel-{action_config.vessel_id}"

    vessel_state = await state_manager.get_state(
        integration_id=integration_id,
        action_id="pull_vessel_tracking",
        source_id=track_id,
    )
    subject_id = vessel_state.get("subject_id")

    deleted = False
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
            continue
        dest_token = auth_config.data.get("token")
        if not dest_token:
            continue

        deleted = deleted or await delete_vessel_from_earthranger(
            track_id=track_id,
            er_base_url=dest_base_url,
            er_token=dest_token,
            subject_id=subject_id,
        )

    if deleted:
        await state_manager.delete_state(
            integration_id=integration_id,
            action_id="pull_vessel_tracking",
            source_id=track_id,
        )
        known_vessels_state = await state_manager.get_state(
            integration_id=integration_id,
            action_id="pull_vessel_tracking",
            source_id="known_vessels",
        )
        updated_ids = [t for t in known_vessels_state.get("track_ids", []) if t != track_id]
        await state_manager.set_state(
            integration_id=integration_id,
            action_id="pull_vessel_tracking",
            state={**known_vessels_state, "track_ids": updated_ids},
            source_id="known_vessels",
        )

    return {"track_id": track_id, "subject_id": subject_id, "deleted": deleted}


@activity_logger()
async def action_clear_vessel_state(
    integration,
    action_config: ClearVesselStateConfiguration,
) -> dict:
    state_manager = IntegrationStateManager()
    integration_id = str(integration.id)

    known_vessels_state = await state_manager.get_state(
        integration_id=integration_id,
        action_id="pull_vessel_tracking",
        source_id="known_vessels",
    )
    track_ids = known_vessels_state.get("track_ids", [])

    for track_id in track_ids:
        await state_manager.delete_state(
            integration_id=integration_id,
            action_id="pull_vessel_tracking",
            source_id=track_id,
        )

    await state_manager.delete_state(
        integration_id=integration_id,
        action_id="pull_vessel_tracking",
        source_id="known_vessels",
    )

    return {"vessels_cleared": len(track_ids)}
