"""Tests for Marine Monitor API client."""
import httpx
import pytest
import respx
from app.actions.marine_monitor import (
    MarineMonitorClient,
    MarineMonitorAuthenticationError,
    MarineMonitorServiceUnreachable,
    MarineMonitorClientError,
    MarineMonitorRateLimitError,
)


@pytest.mark.asyncio
async def test_client_get_track_markers_success(sample_radar_station_response):
    """Test successful fetch of track markers."""
    api_url = "https://m2mobile.protectedseas.net/api/map/42/earthranger"

    async with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{api_url}/trackmarkers").respond(
            status_code=httpx.codes.OK,
            json=sample_radar_station_response,
        )

        async with MarineMonitorClient(api_url=api_url, api_key="test-key") as client:
            result = await client.get_track_markers()

            assert result == sample_radar_station_response
            assert len(result) == 2
            assert result[0]["id"] == 42
            assert result[0]["name"] == "Loreto 2"
            assert len(result[0]["tracks"]) == 1


@pytest.mark.asyncio
async def test_client_get_track_markers_empty():
    """Test fetch when no radar stations are returned."""
    api_url = "https://m2mobile.protectedseas.net/api/map/42/earthranger"

    async with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{api_url}/trackmarkers").respond(
            status_code=httpx.codes.OK,
            json=[],
        )

        async with MarineMonitorClient(api_url=api_url, api_key="test-key") as client:
            result = await client.get_track_markers()

            assert result == []


@pytest.mark.asyncio
async def test_client_authentication_error():
    """Test authentication failure (401)."""
    api_url = "https://m2mobile.protectedseas.net/api/map/42/earthranger"

    async with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{api_url}/trackmarkers").respond(
            status_code=httpx.codes.UNAUTHORIZED,
            json={"error": "Invalid API key"},
        )

        async with MarineMonitorClient(api_url=api_url, api_key="bad-key") as client:
            with pytest.raises(MarineMonitorAuthenticationError):
                await client.get_track_markers()


@pytest.mark.asyncio
async def test_client_forbidden_error():
    """Test forbidden error (403)."""
    api_url = "https://m2mobile.protectedseas.net/api/map/42/earthranger"

    async with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{api_url}/trackmarkers").respond(
            status_code=httpx.codes.FORBIDDEN,
            json={"error": "Access denied"},
        )

        async with MarineMonitorClient(api_url=api_url, api_key="test-key") as client:
            with pytest.raises(MarineMonitorAuthenticationError):
                await client.get_track_markers()


@pytest.mark.asyncio
async def test_client_rate_limit_error():
    """Test rate limit error (429)."""
    api_url = "https://m2mobile.protectedseas.net/api/map/42/earthranger"

    async with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{api_url}/trackmarkers").respond(
            status_code=httpx.codes.TOO_MANY_REQUESTS,
            json={"error": "Rate limit exceeded"},
        )

        async with MarineMonitorClient(api_url=api_url, api_key="test-key") as client:
            with pytest.raises(MarineMonitorRateLimitError):
                await client.get_track_markers()


@pytest.mark.asyncio
async def test_client_service_unavailable():
    """Test service unavailable error (503)."""
    api_url = "https://m2mobile.protectedseas.net/api/map/42/earthranger"

    async with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{api_url}/trackmarkers").respond(
            status_code=httpx.codes.SERVICE_UNAVAILABLE,
        )

        async with MarineMonitorClient(api_url=api_url, api_key="test-key") as client:
            with pytest.raises(MarineMonitorServiceUnreachable):
                await client.get_track_markers()


@pytest.mark.asyncio
async def test_client_bad_gateway():
    """Test bad gateway error (502)."""
    api_url = "https://m2mobile.protectedseas.net/api/map/42/earthranger"

    async with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{api_url}/trackmarkers").respond(
            status_code=httpx.codes.BAD_GATEWAY,
        )

        async with MarineMonitorClient(api_url=api_url, api_key="test-key") as client:
            with pytest.raises(MarineMonitorServiceUnreachable):
                await client.get_track_markers()


@pytest.mark.asyncio
async def test_client_gateway_timeout():
    """Test gateway timeout error (504)."""
    api_url = "https://m2mobile.protectedseas.net/api/map/42/earthranger"

    async with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{api_url}/trackmarkers").respond(
            status_code=httpx.codes.GATEWAY_TIMEOUT,
        )

        async with MarineMonitorClient(api_url=api_url, api_key="test-key") as client:
            with pytest.raises(MarineMonitorServiceUnreachable):
                await client.get_track_markers()


@pytest.mark.asyncio
async def test_client_other_http_error():
    """Test other HTTP error (500)."""
    api_url = "https://m2mobile.protectedseas.net/api/map/42/earthranger"

    async with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{api_url}/trackmarkers").respond(
            status_code=httpx.codes.INTERNAL_SERVER_ERROR,
            text="Internal Server Error",
        )

        async with MarineMonitorClient(api_url=api_url, api_key="test-key") as client:
            with pytest.raises(MarineMonitorClientError) as exc_info:
                await client.get_track_markers()

            assert "HTTP 500" in str(exc_info.value)


@pytest.mark.asyncio
async def test_client_connection_error():
    """Test connection error."""
    api_url = "https://m2mobile.protectedseas.net/api/map/42/earthranger"

    async with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{api_url}/trackmarkers").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        async with MarineMonitorClient(api_url=api_url, api_key="test-key") as client:
            with pytest.raises(MarineMonitorServiceUnreachable) as exc_info:
                await client.get_track_markers()

            assert "Failed to connect" in str(exc_info.value)


@pytest.mark.asyncio
async def test_client_test_connection_success(sample_radar_station_response):
    """Test successful connection test."""
    api_url = "https://m2mobile.protectedseas.net/api/map/42/earthranger"

    async with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{api_url}/trackmarkers").respond(
            status_code=httpx.codes.OK,
            json=sample_radar_station_response,
        )

        async with MarineMonitorClient(api_url=api_url, api_key="test-key") as client:
            result = await client.test_connection()
            assert result is True


@pytest.mark.asyncio
async def test_client_test_connection_failure():
    """Test failed connection test."""
    api_url = "https://m2mobile.protectedseas.net/api/map/42/earthranger"

    async with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{api_url}/trackmarkers").respond(
            status_code=httpx.codes.UNAUTHORIZED,
        )

        async with MarineMonitorClient(api_url=api_url, api_key="bad-key") as client:
            with pytest.raises(MarineMonitorAuthenticationError):
                await client.test_connection()


@pytest.mark.asyncio
async def test_client_url_with_trailing_slash():
    """Test that URL with trailing slash is handled correctly."""
    api_url = "https://m2mobile.protectedseas.net/api/map/42/earthranger/"

    async with respx.mock(assert_all_called=True) as mock:
        # URL should be normalized to not have double slashes
        mock.get("https://m2mobile.protectedseas.net/api/map/42/earthranger/trackmarkers").respond(
            status_code=httpx.codes.OK,
            json=[],
        )

        async with MarineMonitorClient(api_url=api_url, api_key="test-key") as client:
            result = await client.get_track_markers()
            assert result == []


@pytest.mark.asyncio
async def test_client_authorization_header():
    """Test that API key is sent in Authorization header."""
    api_url = "https://m2mobile.protectedseas.net/api/map/42/earthranger"
    api_key = "jK8f3L7hQs9D2bN4vW5mY6uT0xE1rV3P"

    async with respx.mock(assert_all_called=True) as mock:
        route = mock.get(f"{api_url}/trackmarkers").respond(
            status_code=httpx.codes.OK,
            json=[],
        )

        async with MarineMonitorClient(api_url=api_url, api_key=api_key) as client:
            await client.get_track_markers()

            # Verify the Authorization header was sent
            assert route.calls[0].request.headers["Authorization"] == api_key


@pytest.mark.asyncio
async def test_client_url_with_trackmarkers_path():
    """Test that URL with /trackmarkers path is stripped and handled correctly."""
    # URL already includes /trackmarkers which should be removed
    api_url = "https://m2mobile.protectedseas.net/api/map/42/earthranger/trackmarkers"

    async with respx.mock(assert_all_called=True) as mock:
        # The client should strip /trackmarkers and then add it back when calling the endpoint
        mock.get("https://m2mobile.protectedseas.net/api/map/42/earthranger/trackmarkers").respond(
            status_code=httpx.codes.OK,
            json=[],
        )

        async with MarineMonitorClient(api_url=api_url, api_key="test-key") as client:
            result = await client.get_track_markers()
            assert result == []
            # Verify the correct URL was called (without double /trackmarkers)
            assert client.api_url == "https://m2mobile.protectedseas.net/api/map/42/earthranger"
