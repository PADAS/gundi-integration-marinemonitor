"""Marine Monitor API client for vessel tracking data."""
import httpx
from typing import Any

from .errors import (
    MarineMonitorClientError,
    MarineMonitorAuthenticationError,
    MarineMonitorServiceUnreachable,
    MarineMonitorRateLimitError,
)

# Map HTTP status codes to exception types
_STATUS_CODE_EXCEPTIONS: dict[int, type[MarineMonitorClientError]] = {
    401: MarineMonitorAuthenticationError,
    403: MarineMonitorAuthenticationError,
    429: MarineMonitorRateLimitError,
    502: MarineMonitorServiceUnreachable,
    503: MarineMonitorServiceUnreachable,
    504: MarineMonitorServiceUnreachable,
}


class MarineMonitorClient:
    """Async client for Marine Monitor API.

    The Marine Monitor API provides vessel tracking data from radar stations.
    Each radar station can have multiple vessel tracks with position data.
    """

    DEFAULT_CONNECT_TIMEOUT = 10
    DEFAULT_DATA_TIMEOUT = 60

    def __init__(
        self,
        api_url: str,
        api_key: str,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        data_timeout: float = DEFAULT_DATA_TIMEOUT,
    ):
        """Initialize the Marine Monitor client.

        :param api_url: Base URL for the API (e.g., https://m2mobile.protectedseas.net/api/map/42/earthranger)
        :param api_key: API key for authentication (sent in Authorization header)
        :param connect_timeout: Connection timeout in seconds
        :param data_timeout: Data read timeout in seconds
        """
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        session_kwargs = {
            "timeout": httpx.Timeout(data_timeout, connect=connect_timeout),
            "headers": {"Authorization": api_key},
        }
        self.session = httpx.AsyncClient(**session_kwargs)

    async def __aenter__(self):
        await self.session.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.close()

    async def close(self):
        """Close the HTTP session."""
        await self.session.aclose()

    async def _call_api(
        self,
        endpoint: str,
        method: str = "GET",
        params: dict[str, Any] | None = None,
        **kwargs,
    ) -> Any:
        """Make an API call to Marine Monitor.

        :param endpoint: API endpoint path (will be appended to api_url)
        :param method: HTTP method (GET or DELETE)
        :param params: Query parameters
        :return: Parsed JSON response
        :raises MarineMonitorClientError: On API errors
        """
        url = f"{self.api_url}/{endpoint.lstrip('/')}"

        try:
            response = await self.session.request(method, url, params=params, **kwargs)
            response.raise_for_status()
            return response.json() if response.text else {}

        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            exception_class = _STATUS_CODE_EXCEPTIONS.get(status_code)
            if exception_class:
                raise exception_class(response=e.response)
            raise MarineMonitorClientError(
                f"HTTP {status_code}: {e.response.text}",
                response=e.response,
            )
        except httpx.RequestError as e:
            raise MarineMonitorServiceUnreachable(
                f"Failed to connect: {type(e).__name__}: {e}"
            )
        except Exception as e:
            raise MarineMonitorClientError(f"{type(e).__name__}: {e}")

    async def get_track_markers(self) -> list[dict[str, Any]]:
        """Fetch all radar stations with their vessel tracks.

        Returns a list of radar station objects, each containing:
        - id: Radar station ID
        - name: Station name
        - latitude/longitude: Station position
        - tracks: List of vessel tracks with track_detection data

        :return: List of radar station data with tracks
        """
        return await self._call_api("trackmarkers")

    async def test_connection(self) -> bool:
        """Test the API connection and authentication.

        :return: True if connection successful
        :raises MarineMonitorAuthenticationError: If authentication fails
        :raises MarineMonitorServiceUnreachable: If service is unavailable
        """
        await self.get_track_markers()
        return True
