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

from app.actions.configurations import PullVesselTrackingConfiguration
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


async def deactivate_subject_in_earthranger(
    track_id: str,
    er_base_url: str,
    er_token: str,
    subject_id: str | None = None,
) -> bool:
    """Deactivate a subject in EarthRanger by setting is_active to false.

    If subject_id is provided, skips the source/subject lookup and deactivates directly.
    Otherwise:
    1. Gets the source by manufacturer_id (track_id)
    2. Gets all subjects linked to the source
    3. Picks the subject with the most recent last_position_date
    4. Deactivates that subject

    :param track_id: The track ID used as manufacturer_id in ER
    :param er_base_url: The EarthRanger base URL
    :param er_token: The EarthRanger auth token
    :param subject_id: Optional cached subject UUID — skips ER lookup if provided
    :return: True if deactivation was successful, False otherwise
    """
    try:
        async with AsyncERClient(
            service_root=f"{er_base_url}/api/v1.0",
            token=er_token,
        ) as client:
            if not subject_id:
                # Get source by manufacturer_id (track_id)
                try:
                    source_response = await client.get_source_by_manufacturer_id(track_id)
                    source = source_response.get("data", source_response)
                except ERClientNotFound:
                    logger.warning(
                        f"Source not found for track_id '{track_id}', skipping deactivation"
                    )
                    return False

                source_id = source.get("id")
                if not source_id:
                    logger.warning(f"Source response missing 'id' for track_id '{track_id}'")
                    return False

                subjects = await client.get_source_subjects(source_id)
                if not subjects:
                    logger.info(f"No subjects found for source '{source_id}', nothing to deactivate")
                    return False

                subject_to_deactivate = max(subjects, key=get_position_date)
                subject_id = subject_to_deactivate.get("id")

                if not subject_id:
                    logger.warning("Subject missing 'id' field")
                    return False

                if not subject_to_deactivate.get("is_active", True):
                    logger.info(f"Subject '{subject_id}' is already inactive")
                    return True

            await client.patch_subject(subject_id, {"is_active": False})
            logger.info(f"Successfully deactivated subject '{subject_id}' for track '{track_id}'")
            return True

    except Exception as e:
        logger.exception(f"Failed to deactivate subject for track_id '{track_id}': {e}")
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
    track_id = track.get("id", track.get("radar_track_id", "unknown-source"))

    # Use track ID as source identifier, fallback to default if not set
    source_id = f"vessel-{track_id}"

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


async def _handle_stale_subjects(
    state_manager: IntegrationStateManager,
    integration_id: str,
    er_base_url: str,
    er_token: str,
    active_track_ids: set[str],
    now: datetime,
) -> int:
    """Handle deactivation of stale subjects and update state.

    Compares currently active tracks with stored state. Deactivates subjects
    for tracks that are no longer appearing in the API.

    Returns the number of subjects deactivated.
    """
    deactivated_count = 0

    # Get track index to find known track IDs
    # ToDo: Use state manager Groups (redis set) as in the ATS integration, once available in the template repo
    index_state = await state_manager.get_state(
        integration_id=integration_id,
        action_id="pull_vessel_tracking",
        source_id="track_index",
    )
    known_track_ids = set(index_state.get("track_ids", []))

    # Find stale tracks (known but not currently active)
    stale_track_ids = known_track_ids - active_track_ids

    # Deactivate subjects for stale tracks
    for track_id in stale_track_ids:
        logger.info(f"Track '{track_id}' is stale, attempting to deactivate subject")

        track_state = await state_manager.get_state(
            integration_id=integration_id,
            action_id="pull_vessel_tracking",
            source_id=track_id,
        )
        deactivated = await deactivate_subject_in_earthranger(
            track_id=track_id,
            er_base_url=er_base_url,
            er_token=er_token,
            subject_id=track_state.get("subject_id"),
        )

        if deactivated:
            deactivated_count += 1
            # Delete the track's state key
            await state_manager.delete_state(
                integration_id=integration_id,
                action_id="pull_vessel_tracking",
                source_id=track_id,
            )
            logger.info(f"Deactivated and removed track '{track_id}' from state")

    # Update track index with currently active tracks
    await state_manager.set_state(
        integration_id=integration_id,
        action_id="pull_vessel_tracking",
        state={"track_ids": list(active_track_ids), "last_run": now.isoformat()},
        source_id="track_index",
    )

    # Store individual state for each active track, preserving existing fields (e.g. subject_id)
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

    return deactivated_count



@backoff.on_exception(backoff.expo, Exception, max_tries=5, jitter=backoff.full_jitter)
async def _post_observation_to_er(client: AsyncERClient, observation: dict, subject_group_name: Optional[str] = None) -> None:
    payload = {
        "manufacturer_id": observation["source"],
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
    have been processed. Optionally deactivates stale subjects in EarthRanger.
    """
    state_manager = IntegrationStateManager()
    integration_id = str(integration.id)

    results = {
        "observations_extracted": 0,
        "radar_stations_processed": 0,
        "tracks_processed": 0,
        "subjects_deactivated": 0,
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
            logger.warning(f"No destinations configured for integration '{integration_id}', skipping subject deactivation and group assignment")
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
                async with AsyncERClient(
                    service_root=f"{dest_base_url}/api/v1.0",
                    token=dest_token,
                    provider_key=f"gundi_marine_monitor_{integration_id}",
                ) as er_client:
                    for observation in all_observations:
                        await _post_observation_to_er(er_client, observation, action_config.earthranger_subject_group_name)
                results["observations_extracted"] = len(all_observations)
                logger.info(f"Sent {len(all_observations)} observations to EarthRanger at {dest_base_url}")

            results["subjects_deactivated"] += await _handle_stale_subjects(
                state_manager=state_manager,
                integration_id=integration_id,
                er_base_url=dest_base_url,
                er_token=dest_token,
                active_track_ids=active_track_ids,
                now=now,
            )

    return results
