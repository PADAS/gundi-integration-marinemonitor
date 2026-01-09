"""Marine Monitor API client module."""
from .client import MarineMonitorClient
from .errors import (
    MarineMonitorClientError,
    MarineMonitorAuthenticationError,
    MarineMonitorServiceUnreachable,
    MarineMonitorRateLimitError,
)

__all__ = [
    "MarineMonitorClient",
    "MarineMonitorClientError",
    "MarineMonitorAuthenticationError",
    "MarineMonitorServiceUnreachable",
    "MarineMonitorRateLimitError",
]
