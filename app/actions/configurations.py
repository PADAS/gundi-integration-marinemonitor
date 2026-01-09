"""Configuration models for Marine Monitor integration actions."""
from pydantic import Field, SecretStr
from app.actions.core import PullActionConfiguration


class PullVesselTrackingConfiguration(PullActionConfiguration):
    """Configuration for pulling vessel tracking data from Marine Monitor."""

    api_url: str = Field(
        ...,
        title="API URL",
        description=(
            "Full API URL for Marine Monitor including account ID. "
            "Example: https://m2mobile.protectedseas.net/api/map/42/earthranger"
        ),
        min_length=1,
    )

    api_key: SecretStr = Field(
        ...,
        title="API Key",
        description="API key for authentication (sent in Authorization header).",
    )

    delete_subject_after_minutes: int = Field(
        default=60,
        ge=0,
        le=10080,  # Max 7 days
        title="Delete Subject After (Minutes)",
        description=(
            "Delete vessel subjects from EarthRanger if they haven't been updated "
            "for this many minutes. Set to 0 to disable automatic deletion."
        ),
    )
