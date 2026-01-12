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

    deactivate_subjects_auto: bool = Field(
        default=True,
        title="Auto-deactivate Subjects",
        description=(
            "Automatically deactivate vessel subjects in EarthRanger when they "
            "stop appearing in Marine Monitor API"
        ),
    )

    earthranger_base_url: str = Field(
        ...,
        title="EarthRanger Base URL",
        description=(
            "Base URL for EarthRanger API "
            "(e.g., https://gundi-dev.staging.pamdas.org)"
        ),
        min_length=1,
    )

    earthranger_token: SecretStr = Field(
        ...,
        title="EarthRanger Token",
        description="Authentication token for EarthRanger API",
    )
