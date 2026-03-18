"""Configuration models for Marine Monitor integration actions."""
from typing import Optional

from pydantic import Field, SecretStr
from app.actions.core import PullActionConfiguration, GenericActionConfiguration


class PullVesselTrackingConfiguration(PullActionConfiguration):
    """Configuration for pulling vessel tracking data from Marine Monitor."""

    api_url: str = Field(
        ...,
        title="Marine Monitor API URL",
        description=(
            "Full Marine Monitor API URL. "
            "Example: https://m2mobile.protectedseas.net/api/map/0/earthranger/trackmarkers"
        ),
        min_length=1,
    )

    api_key: SecretStr = Field(
        ...,
        title="Marine Monitor API Key",
        description="Marine Monitor API key for authentication",
    )

    earthranger_subject_group_name: Optional[str] = Field(
        None,
        title="EarthRanger Subject Group Name",
        description="Name of the EarthRanger subject group to assign vessel subjects to. The group will be created if it does not exist.",
    )

    minimal_confidence: float = Field(
        default=0.1,
        title="Minimal Confidence",
        description=(
            "Minimum confidence threshold for tracks (0.0 to 1.0). "
            "Tracks with confidence below this value will be filtered out."
        ),
        ge=0.0,
        le=1.0,
    )


class GetVesselStateConfiguration(GenericActionConfiguration):
    """No configuration needed — returns current vessel state from Redis."""
    pass


class DeleteVesselConfiguration(GenericActionConfiguration):
    vessel_id: str = Field(
        ...,
        title="Vessel ID",
        description="The vessel ID to delete (e.g. 4134321). Use action_get_vessel_state to find known IDs.",
    )


class ClearVesselStateConfiguration(GenericActionConfiguration):
    """No configuration needed — clears all vessel state from Redis."""
    pass
