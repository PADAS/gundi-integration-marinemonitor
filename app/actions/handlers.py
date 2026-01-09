"""Action handlers for Marine Monitor integration."""
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from app.actions.configurations import PullVesselTrackingConfiguration
from app.actions.marine_monitor import MarineMonitorClient
from app.services.action_scheduler import crontab_schedule
from app.services.activity_logger import activity_logger, log_action_activity
from app.services.gundi import send_observations_to_gundi
from app.services.state import IntegrationStateManager

logger = logging.getLogger(__name__)


async def delete_subject_from_earthranger(
    subject_id: str,
    integration_id: str,
) -> bool:
    """Placeholder function to delete a subject from EarthRanger.

    TODO: Implement the actual EarthRanger API call to delete a subject.

    :param subject_id: The source ID of the subject to delete
    :param integration_id: The integration ID for authentication
    :return: True if deletion was successful
    """
    logger.info(
        f"[PLACEHOLDER] Would delete subject '{subject_id}' from EarthRanger "
        f"for integration {integration_id}"
    )
    # TODO: Implement EarthRanger API call:
    # DELETE /api/v1.0/subjects/{subject_id}
    # or use appropriate EarthRanger client
    return True


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
    radar_track_id = track.get("radar_track_id")

    # Use radar_track_id as source identifier, fallback to track id
    source_id = str(radar_track_id) if radar_track_id else f"track-{track.get('id')}"

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
    _add_optional_field(additional, "radar_track_id", radar_track_id)
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
        "subject_type": "vessel",
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
) -> dict[str, Any] | None:
    """Process a single track and return observation if valid.

    Returns None if the track has no valid timestamp.
    """
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
    active_track_ids: set[str],
    delete_after_minutes: int,
    now: datetime,
) -> int:
    """Handle deletion of stale subjects and update state.

    Returns the number of subjects deleted.
    """
    state = await state_manager.get_state(
        integration_id=integration_id,
        action_id="pull_vessel_tracking",
        source_id="tracked_subjects",
    )
    known_subjects = state.get("subjects", {})

    # Start with currently active tracks
    updated_subjects = {track_id: now.isoformat() for track_id in active_track_ids}

    # Process previously known subjects that are no longer active
    cutoff_time = now - timedelta(minutes=delete_after_minutes)
    deleted_count = 0

    for subject_id, last_seen_str in known_subjects.items():
        if subject_id in active_track_ids:
            continue

        try:
            last_seen = parse_timestamp(last_seen_str)
            if last_seen < cutoff_time:
                deleted = await delete_subject_from_earthranger(
                    subject_id=subject_id,
                    integration_id=integration_id,
                )
                if deleted:
                    deleted_count += 1
                    logger.info(f"Deleted stale subject: {subject_id}")
            else:
                updated_subjects[subject_id] = last_seen_str
        except (ValueError, TypeError):
            updated_subjects[subject_id] = last_seen_str

    await state_manager.set_state(
        integration_id=integration_id,
        action_id="pull_vessel_tracking",
        state={"subjects": updated_subjects, "last_run": now.isoformat()},
        source_id="tracked_subjects",
    )

    return deleted_count


@crontab_schedule("*/5 * * * *")  # Run every 5 minutes
@activity_logger()
async def action_pull_vessel_tracking(
    integration,
    action_config: PullVesselTrackingConfiguration,
) -> dict[str, int]:
    """Pull vessel tracking data from Marine Monitor API and send to Gundi.

    Fetches radar station data with vessel tracks and transforms them to
    Gundi observations. Uses state management to track which observations
    have been processed. Optionally deletes stale subjects from EarthRanger.
    """
    state_manager = IntegrationStateManager()
    integration_id = str(integration.id)

    results = {
        "observations_extracted": 0,
        "radar_stations_processed": 0,
        "tracks_processed": 0,
        "subjects_deleted": 0,
        "radar_stations_failed": 0,
    }

    try:
        async with MarineMonitorClient(
            api_url=action_config.api_url,
            api_key=action_config.api_key.get_secret_value(),
        ) as client:
            radar_stations = await client.get_track_markers()

            await log_action_activity(
                integration_id=integration_id,
                action_id="pull_vessel_tracking",
                title=f"Fetched {len(radar_stations)} radar stations from Marine Monitor",
                level="INFO",
                data={
                    "radar_stations_count": len(radar_stations),
                    "api_url": action_config.api_url,
                },
            )

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
                        observation = _process_track(track, radar_station)
                        if observation:
                            all_observations.append(observation)
                            active_track_ids.add(observation["source"])
                            results["tracks_processed"] += 1

                    results["radar_stations_processed"] += 1

                except Exception as e:
                    logger.exception(f"Failed to process radar station {radar_id}: {e}")
                    results["radar_stations_failed"] += 1

            if all_observations:
                await send_observations_to_gundi(
                    observations=all_observations,
                    integration_id=integration_id,
                )
                results["observations_extracted"] = len(all_observations)
                logger.info(f"Sent {len(all_observations)} observations to Gundi")

            if action_config.delete_subject_after_minutes > 0:
                results["subjects_deleted"] = await _handle_stale_subjects(
                    state_manager=state_manager,
                    integration_id=integration_id,
                    active_track_ids=active_track_ids,
                    delete_after_minutes=action_config.delete_subject_after_minutes,
                    now=now,
                )

    except Exception as e:
        logger.exception(f"Failed to fetch data from Marine Monitor API: {e}")
        raise

    await log_action_activity(
        integration_id=integration_id,
        action_id="pull_vessel_tracking",
        title=f"Completed: {results['observations_extracted']} observations, {results['subjects_deleted']} subjects deleted",
        level="INFO",
        data=results,
    )

    return results
