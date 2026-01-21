"""Unit tests for Shopmonkey client with retry logic and error handling."""

import pytest
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from shopmonkey_client import (
    ShopmonkeyClient,
    ShopmonkeyAPIError,
    ShopmonkeyTimeoutError,
    ShopmonkeyNetworkError,
)


class TestShopmonkeyClientInit:
    """Tests for ShopmonkeyClient initialization."""

    def test_requires_api_token(self):
        """Should raise ValueError when API token is not provided."""
        with patch.dict("os.environ", {"SHOPMONKEY_API_TOKEN": ""}, clear=False):
            with pytest.raises(ValueError, match="SHOPMONKEY_API_TOKEN is required"):
                ShopmonkeyClient(api_token=None)

    def test_accepts_api_token(self):
        """Should accept API token via constructor."""
        client = ShopmonkeyClient(api_token="test-token")
        assert client.api_token == "test-token"

    def test_default_timeout(self):
        """Should have default timeout of 30 seconds."""
        client = ShopmonkeyClient(api_token="test-token")
        assert client.timeout == 30.0

    def test_custom_timeout(self):
        """Should accept custom timeout."""
        client = ShopmonkeyClient(api_token="test-token", timeout=60.0)
        assert client.timeout == 60.0


class TestShopmonkeyAPIError:
    """Tests for custom exception classes."""

    def test_api_error_with_status_code(self):
        """Should store status code and response body."""
        error = ShopmonkeyAPIError("Error message", status_code=400, response_body='{"error": "bad request"}')
        assert str(error) == "Error message"
        assert error.status_code == 400
        assert error.response_body == '{"error": "bad request"}'

    def test_timeout_error(self):
        """Should create timeout error with default message."""
        error = ShopmonkeyTimeoutError()
        assert "timed out" in str(error).lower()

    def test_network_error(self):
        """Should create network error with default message."""
        error = ShopmonkeyNetworkError()
        assert "network" in str(error).lower()


class TestShopmonkeyClientRetry:
    """Tests for retry logic in ShopmonkeyClient."""

    @pytest.mark.asyncio
    async def test_retries_on_timeout(self):
        """Should retry on timeout exceptions."""
        client = ShopmonkeyClient(api_token="test-token")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": []}
        mock_response.raise_for_status = MagicMock()

        # First call times out, second succeeds
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            side_effect=[
                httpx.TimeoutException("Connection timed out"),
                mock_response,
            ]
        )

        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client._request("GET", "/test")
            assert result == {"data": []}
            assert mock_client.request.call_count == 2

        await client.close()

    @pytest.mark.asyncio
    async def test_retries_on_network_error(self):
        """Should retry on network errors."""
        client = ShopmonkeyClient(api_token="test-token")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": "success"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            side_effect=[
                httpx.NetworkError("Connection reset"),
                mock_response,
            ]
        )

        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client._request("GET", "/test")
            assert result == {"data": "success"}
            assert mock_client.request.call_count == 2

        await client.close()

    @pytest.mark.asyncio
    async def test_does_not_retry_on_client_error(self):
        """Should not retry on 4xx client errors."""
        client = ShopmonkeyClient(api_token="test-token")

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = '{"error": "bad request"}'
        mock_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "400 Bad Request",
                request=MagicMock(),
                response=mock_response,
            )
        )

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)

        with patch.object(client, "_get_client", return_value=mock_client):
            with pytest.raises(ShopmonkeyAPIError) as exc_info:
                await client._request("GET", "/test")

            assert exc_info.value.status_code == 400
            # Should only be called once (no retry)
            assert mock_client.request.call_count == 1

        await client.close()

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self):
        """Should raise exception after max retries exhausted."""
        client = ShopmonkeyClient(api_token="test-token")

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            side_effect=httpx.TimeoutException("Always times out")
        )

        with patch.object(client, "_get_client", return_value=mock_client):
            with pytest.raises(ShopmonkeyTimeoutError):
                await client._request("GET", "/test")

            # Should have tried 3 times (max retries)
            assert mock_client.request.call_count == 3

        await client.close()


class TestShopmonkeyClientMethods:
    """Tests for ShopmonkeyClient API methods."""

    @pytest.mark.asyncio
    async def test_get_canned_service_returns_none_on_404(self):
        """Should return None when service not found (404)."""
        client = ShopmonkeyClient(api_token="test-token")

        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = '{"error": "not found"}'
        mock_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "404 Not Found",
                request=MagicMock(),
                response=mock_response,
            )
        )

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)

        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.get_canned_service("nonexistent-id")
            assert result is None

        await client.close()

    @pytest.mark.asyncio
    async def test_get_canned_service_raises_on_other_errors(self):
        """Should raise on non-404 errors."""
        client = ShopmonkeyClient(api_token="test-token")

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = '{"error": "internal error"}'
        mock_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "500 Internal Server Error",
                request=MagicMock(),
                response=mock_response,
            )
        )

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)

        with patch.object(client, "_get_client", return_value=mock_client):
            with pytest.raises(ShopmonkeyAPIError) as exc_info:
                await client.get_canned_service("service-id")
            assert exc_info.value.status_code == 500

        await client.close()

    @pytest.mark.asyncio
    async def test_health_check_returns_true_on_success(self):
        """Should return True when API is reachable."""
        client = ShopmonkeyClient(api_token="test-token")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": []}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)

        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.health_check()
            assert result is True

        await client.close()

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_failure(self):
        """Should return False when API is not reachable."""
        client = ShopmonkeyClient(api_token="test-token")

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            side_effect=httpx.TimeoutException("Connection timed out")
        )

        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.health_check()
            assert result is False

        await client.close()


class TestShopmonkeyClientLocationId:
    """Tests for location ID handling."""

    @pytest.mark.asyncio
    async def test_includes_location_id_in_requests(self):
        """Should include locationId in API requests when configured."""
        client = ShopmonkeyClient(api_token="test-token", location_id="loc-123")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": []}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)

        with patch.object(client, "_get_client", return_value=mock_client):
            await client.get_bookable_canned_services()

            # Check that locationId was included in params
            call_args = mock_client.request.call_args
            params = call_args.kwargs.get("params", {})
            assert params.get("locationId") == "loc-123"

        await client.close()
