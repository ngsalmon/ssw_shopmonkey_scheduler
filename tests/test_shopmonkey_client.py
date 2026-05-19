"""Unit tests for Shopmonkey client with retry logic and error handling."""

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from shopmonkey_client import (
    ShopmonkeyAPIError,
    ShopmonkeyClient,
    ShopmonkeyNetworkError,
    ShopmonkeyTimeoutError,
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
        error = ShopmonkeyAPIError(
            "Error message", status_code=400, response_body='{"error": "bad request"}'
        )
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
        mock_client.request = AsyncMock(side_effect=httpx.TimeoutException("Always times out"))

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
        mock_client.request = AsyncMock(side_effect=httpx.TimeoutException("Connection timed out"))

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


class TestFindOrCreateCustomer:
    """Tests for find_or_create_customer.

    Anne's bug (2026-05-19): every online booking attached to whichever
    customer sorted first by id because `where: {"email": ...}` was silently
    ignored by Shopmonkey (emails live as a sub-resource). The fix queries
    by firstName + lastName (a top-level filter Shopmonkey honors) and then
    walks the emails/phoneNumbers arrays in-memory.
    """

    @staticmethod
    def _mock(return_data: Any) -> MagicMock:
        m = MagicMock(status_code=200, json=MagicMock(return_value={"data": return_data}))
        m.raise_for_status = MagicMock()
        return m

    @pytest.mark.asyncio
    async def test_searches_by_name_not_by_email(self):
        """Lookup must use firstName/lastName, not email (which is broken)."""
        client = ShopmonkeyClient(api_token="test-token")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=[self._mock([]), self._mock({"id": "cust-1"})])
        with patch.object(client, "_get_client", return_value=mock_client):
            await client.find_or_create_customer(
                first_name="Jane",
                last_name="Doe",
                email="jane@example.com",
                phone="555-1234",
            )
        lookup_params = mock_client.request.call_args_list[0].kwargs["params"]
        where = json.loads(lookup_params["where"])
        assert where == {"firstName": "Jane", "lastName": "Doe"}
        await client.close()

    @pytest.mark.asyncio
    async def test_reuses_when_email_matches_subresource(self):
        """Returns the existing record when one of its sub-resource emails matches."""
        client = ShopmonkeyClient(api_token="test-token")
        existing = {
            "id": "cust-existing",
            "firstName": "Jane",
            "lastName": "Doe",
            "emails": [{"email": "jane@example.com", "primary": True}],
            "phoneNumbers": [{"number": "+15551234567", "primary": True}],
        }
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=self._mock([existing]))
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.find_or_create_customer(
                first_name="Jane", last_name="Doe", email="JANE@example.com"
            )
        assert result == existing
        # Exactly one request - no POST should follow.
        assert mock_client.request.call_count == 1
        await client.close()

    @pytest.mark.asyncio
    async def test_creates_new_when_name_matches_but_email_differs(self):
        """Two real people share a name - don't attach to the wrong one."""
        client = ShopmonkeyClient(api_token="test-token")
        existing_other = {
            "id": "cust-other-jane",
            "firstName": "Jane",
            "lastName": "Doe",
            "emails": [{"email": "different@example.com", "primary": True}],
            "phoneNumbers": [{"number": "+19998887777", "primary": True}],
        }
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            side_effect=[
                self._mock([existing_other]),
                self._mock({"id": "cust-new"}),
            ]
        )
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.find_or_create_customer(
                first_name="Jane",
                last_name="Doe",
                email="newjane@example.com",
                phone="555-1234567",
            )
        assert result == {"id": "cust-new"}
        post_call = mock_client.request.call_args_list[-1]
        body = post_call.kwargs["json"]
        # Must use sub-resource arrays - top-level email/phone are dropped by
        # the API.
        assert body["customerType"] == "Customer"
        assert body["emails"] == [{"email": "newjane@example.com", "primary": True}]
        assert body["phoneNumbers"] == [{"number": "+15551234567", "primary": True}]
        assert "email" not in body  # top-level field is silently dropped
        assert "phone" not in body
        await client.close()

    @pytest.mark.asyncio
    async def test_phone_normalized_to_e164_us(self):
        """10-digit US numbers normalize to +1XXXXXXXXXX."""
        client = ShopmonkeyClient(api_token="test-token")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            side_effect=[self._mock([]), self._mock({"id": "cust-new"})]
        )
        with patch.object(client, "_get_client", return_value=mock_client):
            await client.find_or_create_customer(
                first_name="N", last_name="N", phone="(555) 123-4567"
            )
        body = mock_client.request.call_args_list[-1].kwargs["json"]
        assert body["phoneNumbers"] == [{"number": "+15551234567", "primary": True}]
        await client.close()

    @pytest.mark.asyncio
    async def test_phone_match_ignores_formatting(self):
        """A stored '+15551234567' must match a submitted '555-123-4567'."""
        client = ShopmonkeyClient(api_token="test-token")
        existing = {
            "id": "cust-existing",
            "firstName": "Jane",
            "lastName": "Doe",
            "emails": [],
            "phoneNumbers": [{"number": "+15551234567", "primary": True}],
        }
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=self._mock([existing]))
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.find_or_create_customer(
                first_name="Jane", last_name="Doe", phone="555-123-4567"
            )
        assert result == existing
        await client.close()
