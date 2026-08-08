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


def _ok(data: Any, meta: dict[str, Any] | None = None) -> MagicMock:
    """A successful Shopmonkey response envelope ({"data": ..., "meta": ...})."""
    m = MagicMock(status_code=200)
    body: dict[str, Any] = {"data": data}
    if meta is not None:
        body["meta"] = meta
    m.json.return_value = body
    m.raise_for_status = MagicMock()
    return m


def _err(status_code: int) -> MagicMock:
    """A response that raises HTTPStatusError the way httpx does for 4xx/5xx."""
    m = MagicMock(status_code=status_code)
    m.text = f'{{"error": {status_code}}}'
    m.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(f"{status_code} error", request=MagicMock(), response=m)
    )
    return m


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


class TestGetAppointmentsForDate:
    """Regression coverage for the where-filter syntax that broke conflict
    detection on 2026-05-20.

    Shopmonkey's GET /v3/appointment silently ignores MongoDB-style
    operators with the `$` prefix - the request succeeds but returns ~100
    unfiltered rows, so our availability check thought the shop was empty
    and double-booked customers. Operators WITHOUT the `$` prefix work.
    """

    def _ok(self, data):
        m = MagicMock()
        m.status_code = 200
        m.json.return_value = {"data": data, "meta": {"hasMore": False, "total": len(data)}}
        m.raise_for_status = MagicMock()
        return m

    @pytest.mark.asyncio
    async def test_where_filter_uses_no_dollar_prefix(self):
        """Operators must be `gte`/`lt`, not `$gte`/`$lt`. The server quietly
        drops $-prefixed operators."""
        client = ShopmonkeyClient(api_token="test-token")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=self._ok([]))
        with patch.object(client, "_get_client", return_value=mock_client):
            await client.get_appointments_for_date("2026-05-27")
        call = mock_client.request.call_args
        params = call.kwargs["params"]
        assert "where" in params
        where = json.loads(params["where"])
        assert "startDate" in where
        # The key assertion: no $-prefixed operators leak through.
        operators = where["startDate"]
        assert "$gte" not in operators, "remove $ prefix; server ignores it"
        assert "$lt" not in operators
        assert "gte" in operators
        assert "lt" in operators
        # Bounds cover the requested local day in UTC.
        assert operators["gte"].startswith("2026-05-27T")
        assert operators["lt"].startswith("2026-05-27T")
        await client.close()

    @pytest.mark.asyncio
    async def test_caps_limit_at_100(self):
        """Shopmonkey hard-caps appointment list at 100 rows; we must send
        that limit so callers can detect overflow via the meta block."""
        client = ShopmonkeyClient(api_token="test-token")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=self._ok([]))
        with patch.object(client, "_get_client", return_value=mock_client):
            await client.get_appointments_for_date("2026-05-27")
        params = mock_client.request.call_args.kwargs["params"]
        assert params.get("limit") == "100"
        await client.close()


class TestGetBusyTechsForAppointments:
    """Coverage for the appointment→order→service→labor→technicianId walk
    that powers per-tech availability. Live probe on 2026-05-20 confirmed
    96% of upcoming bookings carry a labor.technicianId; this tested behavior
    is what extracts those tech IDs into the conflict-detection pipeline.
    """

    def _services_response(self, services):
        m = MagicMock()
        m.status_code = 200
        m.json.return_value = {"data": services, "meta": {"hasMore": False, "total": len(services)}}
        m.raise_for_status = MagicMock()
        return m

    @pytest.mark.asyncio
    async def test_collects_tech_ids_from_labors(self):
        client = ShopmonkeyClient(api_token="test-token")
        mock_client = AsyncMock()
        # Two appointments, two distinct orders, each with a labor-tech.
        mock_client.request = AsyncMock(
            side_effect=[
                self._services_response([{"labors": [{"technicianId": "tech_alex"}]}]),
                self._services_response(
                    [
                        {"labors": [{"technicianId": "tech_cam"}]},
                        {"labors": [{"technicianId": "tech_dave"}]},
                    ]
                ),
            ]
        )
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.get_busy_techs_for_appointments(
                [
                    {"id": "appt_1", "orderId": "ord_a"},
                    {"id": "appt_2", "orderId": "ord_b"},
                ]
            )
        assert result == {"appt_1": {"tech_alex"}, "appt_2": {"tech_cam", "tech_dave"}}
        await client.close()

    @pytest.mark.asyncio
    async def test_time_off_block_reports_its_tech_without_order_fetch(self):
        """A vacation / "Mina out" block has no orderId, so there's no order to
        walk - but `technicians[]` still names who is out, and that tech must
        be reported as occupied."""
        client = ShopmonkeyClient(api_token="test-token")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock()
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.get_busy_techs_for_appointments(
                [
                    {
                        "id": "appt_block",
                        "orderId": None,
                        "technicians": [{"id": "tech_mina"}],
                    }
                ]
            )
        assert result == {"appt_block": {"tech_mina"}}
        mock_client.request.assert_not_called()
        await client.close()

    @pytest.mark.asyncio
    async def test_block_naming_no_tech_reports_empty(self):
        """A shop-wide entry ("Cars & Coffee") names nobody and has no order -
        there is no one to block, so it contributes no techs."""
        client = ShopmonkeyClient(api_token="test-token")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock()
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.get_busy_techs_for_appointments(
                [{"id": "appt_block", "orderId": None, "technicians": []}]
            )
        assert result == {"appt_block": set()}
        mock_client.request.assert_not_called()
        await client.close()

    @pytest.mark.asyncio
    async def test_failed_order_fetch_falls_back_to_technicians_field(self):
        """When the order fetch dies we must still keep the assignment the
        appointment row already gave us - degrading to "nobody is busy" here
        would hand out a slot to a tech who is demonstrably occupied."""
        client = ShopmonkeyClient(api_token="test-token")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=ShopmonkeyAPIError("boom", status_code=500))
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.get_busy_techs_for_appointments(
                [
                    {
                        "id": "appt_1",
                        "orderId": "ord_a",
                        "technicians": [{"id": "tech_row"}],
                    }
                ]
            )
        assert result == {"appt_1": {"tech_row"}}
        await client.close()

    @pytest.mark.asyncio
    async def test_appointment_without_id_is_skipped(self):
        """A row with no id can't be keyed, so it's dropped rather than
        crashing the whole availability check."""
        client = ShopmonkeyClient(api_token="test-token")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock()
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.get_busy_techs_for_appointments(
                [{"orderId": None, "technicians": [{"id": "tech_x"}]}]
            )
        assert result == {}
        await client.close()

    @pytest.mark.asyncio
    async def test_unions_technicians_field_with_labor_walk(self):
        """The two sources disagree in both directions on live data, so the
        result must be their union - taking either alone loses assignments."""
        client = ShopmonkeyClient(api_token="test-token")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            return_value=self._services_response([{"labors": [{"technicianId": "tech_walk"}]}])
        )
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.get_busy_techs_for_appointments(
                [
                    {
                        "id": "appt_1",
                        "orderId": "ord_a",
                        "technicians": [{"id": "tech_row"}],
                    }
                ]
            )
        assert result == {"appt_1": {"tech_row", "tech_walk"}}
        await client.close()

    @pytest.mark.asyncio
    async def test_returns_empty_set_when_labors_have_no_tech(self):
        """Order/service exists but no labor has technicianId set (~4% of
        bookings). Caller treats this as 'unattributed' overlap and reduces
        shop capacity by 1 conservatively."""
        client = ShopmonkeyClient(api_token="test-token")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            return_value=self._services_response(
                [{"labors": [{"name": "Some labor"}]}]  # no technicianId
            )
        )
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.get_busy_techs_for_appointments(
                [{"id": "appt_1", "orderId": "ord_a"}]
            )
        assert result == {"appt_1": set()}
        await client.close()


class TestClientLifecycle:
    """The httpx client is built once and shared.

    A fresh AsyncClient per request would leak sockets and drop connection
    reuse under the booking burst the widget generates.
    """

    @pytest.mark.asyncio
    async def test_reuses_one_httpx_client_and_sends_bearer_auth(self):
        client = ShopmonkeyClient(api_token="tok-123", base_url="https://api.example.com/")
        first = await client._get_client()
        second = await client._get_client()

        assert first is second
        # Shopmonkey rejects anything but `Bearer <token>`.
        assert first.headers["authorization"] == "Bearer tok-123"
        # Trailing slash must be stripped so "/v3/x" doesn't become "//v3/x".
        assert str(first.base_url) == "https://api.example.com"
        assert first.timeout.read == 30.0

        await client.close()

    @pytest.mark.asyncio
    async def test_custom_timeout_reaches_httpx(self):
        """The configured timeout has to actually govern the socket, otherwise
        `timeout=` is decoration and slow Shopmonkey calls hang the booking."""
        client = ShopmonkeyClient(api_token="tok", timeout=7.5)
        assert (await client._get_client()).timeout.read == 7.5
        await client.close()

    @pytest.mark.asyncio
    async def test_close_shuts_down_and_allows_reconnect(self):
        client = ShopmonkeyClient(api_token="tok")
        first = await client._get_client()
        await client.close()
        assert first.is_closed
        # A closed client must not be handed out again - httpx raises on reuse.
        second = await client._get_client()
        assert second is not first
        assert not second.is_closed
        await client.close()

    @pytest.mark.asyncio
    async def test_close_without_a_client_is_a_noop(self):
        """close() runs in FastAPI shutdown even if no request was ever made."""
        client = ShopmonkeyClient(api_token="tok")
        await client.close()
        assert client._client is None
        await client.close()
        assert client._client is None


class TestNormalizePhone:
    """Shopmonkey stores phone numbers in E.164; a badly formatted number is
    rejected on create, which fails the whole booking."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            (None, None),
            ("", None),
            # 10 US digits get the implicit +1.
            ("5551234567", "+15551234567"),
            ("(555) 123-4567", "+15551234567"),
            # 11 digits starting with 1 is already a US number.
            ("1-555-123-4567", "+15551234567"),
            # An explicit + wins: don't prepend a US country code to a UK number.
            ("+44 20 7946 0958", "+442079460958"),
            ("+1 (555) 123-4567", "+15551234567"),
            # Not confidently normalizable - pass through what the user typed
            # rather than inventing a country code.
            ("555-1234", "555-1234"),
            ("no phone", "no phone"),
        ],
    )
    def test_normalization(self, raw, expected):
        assert ShopmonkeyClient._normalize_phone(raw) == expected


class TestGetCannedService:
    @pytest.mark.asyncio
    async def test_unwraps_the_data_envelope(self):
        """Callers read `service["labels"]` directly, so the envelope must be
        stripped - returning the whole body would break department lookup."""
        client = ShopmonkeyClient(api_token="test-token")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            return_value=_ok({"id": "svc-1", "name": "Oil Change", "labels": [{"name": "Quick"}]})
        )
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.get_canned_service("svc-1")
        assert result == {"id": "svc-1", "name": "Oil Change", "labels": [{"name": "Quick"}]}
        assert mock_client.request.call_args.kwargs["url"] == "/v3/canned_service/svc-1"
        await client.close()


class TestFindOrCreateCustomerExtras:
    """Remaining branches of the find-vs-create decision."""

    @pytest.mark.asyncio
    async def test_reuses_same_name_match_when_no_contact_info_given(self):
        """With nothing to verify against, a same-name record is the best we
        have - creating a duplicate would fragment the customer's history."""
        client = ShopmonkeyClient(api_token="test-token")
        existing = {"id": "cust-1", "firstName": "Jane", "lastName": "Doe"}
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_ok([existing]))
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.find_or_create_customer(first_name="Jane", last_name="Doe")
        assert result == existing
        assert mock_client.request.call_count == 1  # no POST
        await client.close()

    @pytest.mark.asyncio
    async def test_ignores_rows_whose_name_does_not_match(self):
        """If Shopmonkey ever stops honoring the where filter it returns an
        unfiltered page - taking row 0 blindly is exactly the misattribution
        bug this function exists to prevent."""
        client = ShopmonkeyClient(api_token="test-token")
        wrong_person = {"id": "cust-other", "firstName": "John", "lastName": "Smith"}
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=[_ok([wrong_person]), _ok({"id": "cust-new"})])
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.find_or_create_customer(first_name="Jane", last_name="Doe")
        assert result == {"id": "cust-new"}
        created = mock_client.request.call_args_list[-1].kwargs["json"]
        assert created["firstName"] == "Jane"
        assert created["lastName"] == "Doe"
        await client.close()

    @pytest.mark.asyncio
    async def test_name_prefilter_also_applies_on_the_email_verified_path(self):
        """The realistic widget path always supplies an email, and the contact
        match must only ever run against same-name rows.

        A shared/family email ("jane@example.com" on John Smith's record) plus
        an unfiltered page is exactly Anne's 2026-05-19 misattribution: the
        booking would attach to John Smith because his sub-resource email
        matched. The name pre-filter is what stops it.
        """
        client = ShopmonkeyClient(api_token="test-token")
        wrong_person = {
            "id": "cust-other",
            "firstName": "John",
            "lastName": "Smith",
            "emails": [{"email": "jane@example.com"}],
        }
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=[_ok([wrong_person]), _ok({"id": "cust-new"})])
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.find_or_create_customer(
                first_name="Jane", last_name="Doe", email="jane@example.com"
            )
        # A new record, NOT John Smith's.
        assert result == {"id": "cust-new"}
        assert result["id"] != "cust-other"
        assert mock_client.request.call_count == 2  # lookup then create
        create = mock_client.request.call_args_list[-1]
        assert create.kwargs["method"] == "POST"
        created = create.kwargs["json"]
        assert created["firstName"] == "Jane"
        assert created["lastName"] == "Doe"
        assert created["emails"] == [{"email": "jane@example.com", "primary": True}]
        await client.close()

    @pytest.mark.asyncio
    async def test_name_match_is_case_and_whitespace_insensitive(self):
        """ "jane " typed into the widget is the same person as "Jane"."""
        client = ShopmonkeyClient(api_token="test-token")
        existing = {
            "id": "cust-1",
            "firstName": " Jane",
            "lastName": "doe ",
            "emails": [{"email": "jane@example.com"}],
        }
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_ok([existing]))
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.find_or_create_customer(
                first_name="jane", last_name="Doe", email="jane@example.com"
            )
        assert result == existing
        await client.close()

    @pytest.mark.asyncio
    async def test_location_id_scopes_lookup_and_creation(self):
        """A multi-location account must not read or write another shop's
        customers."""
        client = ShopmonkeyClient(api_token="test-token", location_id="loc-9")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=[_ok([]), _ok({"id": "cust-new"})])
        with patch.object(client, "_get_client", return_value=mock_client):
            await client.find_or_create_customer(first_name="Jane", last_name="Doe")
        lookup, create = mock_client.request.call_args_list
        assert lookup.kwargs["params"]["locationId"] == "loc-9"
        assert create.kwargs["json"]["locationId"] == "loc-9"
        await client.close()

    @pytest.mark.asyncio
    async def test_omits_contact_arrays_when_not_supplied(self):
        """Sending empty/None sub-resource arrays makes Shopmonkey reject the
        create, which fails the booking outright."""
        client = ShopmonkeyClient(api_token="test-token")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=[_ok([]), _ok({"id": "cust-new"})])
        with patch.object(client, "_get_client", return_value=mock_client):
            await client.find_or_create_customer(first_name="Jane", last_name="Doe")
        body = mock_client.request.call_args_list[-1].kwargs["json"]
        assert "emails" not in body
        assert "phoneNumbers" not in body
        await client.close()


class TestFindOrCreateVehicle:
    """A booking attached to the wrong vehicle sends the tech the wrong car,
    and a duplicate vehicle record splits the service history."""

    @pytest.mark.asyncio
    async def test_vin_match_short_circuits_before_any_create(self):
        client = ShopmonkeyClient(api_token="test-token")
        existing = {"id": "veh-1", "vin": "1HGCM82633A004352"}
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_ok([existing]))
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.find_or_create_vehicle(
                customer_id="cust-1",
                year=2003,
                make="Honda",
                model="Accord",
                vin="1HGCM82633A004352",
            )
        assert result == existing
        assert mock_client.request.call_count == 1
        where = json.loads(mock_client.request.call_args.kwargs["params"]["where"])
        assert where == {"vin": "1HGCM82633A004352"}
        await client.close()

    @pytest.mark.asyncio
    async def test_no_vin_skips_the_vin_lookup_entirely(self):
        """Searching `{"vin": null}` would match every VIN-less vehicle in the
        shop and hand back somebody else's car."""
        client = ShopmonkeyClient(api_token="test-token")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=[_ok([]), _ok({"id": "veh-new"})])
        with patch.object(client, "_get_client", return_value=mock_client):
            await client.find_or_create_vehicle(
                customer_id="cust-1", year=2020, make="Subaru", model="WRX"
            )
        assert mock_client.request.call_count == 2
        where = json.loads(mock_client.request.call_args_list[0].kwargs["params"]["where"])
        assert "vin" not in where
        assert where == {
            "customerId": "cust-1",
            "year": 2020,
            "make": "Subaru",
            "model": "WRX",
        }
        await client.close()

    @pytest.mark.asyncio
    async def test_falls_back_to_year_make_model_when_vin_misses(self):
        """A VIN typo must not orphan the customer's existing car."""
        client = ShopmonkeyClient(api_token="test-token")
        existing = {
            "id": "veh-1",
            "customerId": "cust-1",
            "year": 2020,
            "make": "Subaru",
            "model": "WRX",
        }
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=[_ok([]), _ok([existing])])
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.find_or_create_vehicle(
                customer_id="cust-1", year=2020, make="Subaru", model="WRX", vin="BADVIN"
            )
        assert result == existing
        # VIN search then year/make/model search - and no create.
        assert mock_client.request.call_count == 2
        assert all(c.kwargs["method"] == "GET" for c in mock_client.request.call_args_list)
        # The fallback search is scoped to this customer, not the whole shop.
        where = json.loads(mock_client.request.call_args_list[1].kwargs["params"]["where"])
        assert where["customerId"] == "cust-1"
        await client.close()

    @pytest.mark.asyncio
    async def test_creates_with_required_size_and_vin(self):
        """`size` is mandatory on POST /v3/vehicle; omitting it 400s and the
        booking dies after the customer was already created."""
        client = ShopmonkeyClient(api_token="test-token", location_id="loc-9")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=[_ok([]), _ok([]), _ok({"id": "veh-new"})])
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.find_or_create_vehicle(
                customer_id="cust-1",
                year=2020,
                make="Subaru",
                model="WRX",
                vin="JF1VA1B60L9800001",
            )
        assert result == {"id": "veh-new"}
        create = mock_client.request.call_args_list[-1]
        assert create.kwargs["method"] == "POST"
        assert create.kwargs["url"] == "/v3/vehicle"
        assert create.kwargs["json"] == {
            "customerId": "cust-1",
            "year": 2020,
            "make": "Subaru",
            "model": "WRX",
            "size": "LightDuty",
            "vin": "JF1VA1B60L9800001",
            "locationId": "loc-9",
        }
        await client.close()

    @pytest.mark.asyncio
    async def test_create_omits_vin_key_when_none(self):
        """Posting `"vin": null` is rejected by the API."""
        client = ShopmonkeyClient(api_token="test-token")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=[_ok([]), _ok({"id": "veh-new"})])
        with patch.object(client, "_get_client", return_value=mock_client):
            await client.find_or_create_vehicle(
                customer_id="cust-1", year=2020, make="Subaru", model="WRX"
            )
        assert "vin" not in mock_client.request.call_args_list[-1].kwargs["json"]
        await client.close()


class TestCreateAppointment:
    """The appointment IS the booking - a malformed body means the customer
    gets a confirmation number for a slot that isn't on the shop's calendar."""

    @pytest.mark.asyncio
    async def test_sends_required_fields_and_defaults(self):
        client = ShopmonkeyClient(api_token="test-token")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_ok({"id": "appt-1"}))
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.create_appointment(
                customer_id="cust-1",
                vehicle_id="veh-1",
                start_date="2026-06-01T14:00:00Z",
                end_date="2026-06-01T16:00:00Z",
            )
        assert result == {"id": "appt-1"}
        call = mock_client.request.call_args
        assert call.kwargs["method"] == "POST"
        assert call.kwargs["url"] == "/v3/appointment"
        assert call.kwargs["json"] == {
            "customerId": "cust-1",
            "vehicleId": "veh-1",
            "startDate": "2026-06-01T14:00:00Z",
            "endDate": "2026-06-01T16:00:00Z",
            "color": "blue",
            "name": "Online Booking",
        }
        await client.close()

    @pytest.mark.asyncio
    async def test_maps_optional_arguments_onto_api_field_names(self):
        """`notes`->`note` and `technician_id`->`technicianId`; a wrong key is
        silently dropped, so the appointment lands unassigned."""
        client = ShopmonkeyClient(api_token="test-token", location_id="loc-9")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_ok({"id": "appt-1"}))
        with patch.object(client, "_get_client", return_value=mock_client):
            await client.create_appointment(
                customer_id="cust-1",
                vehicle_id="veh-1",
                start_date="2026-06-01T14:00:00Z",
                end_date="2026-06-01T16:00:00Z",
                title="Oil Change",
                notes="Customer waiting",
                technician_id="tech-7",
                color="green",
                order_id="ord-3",
            )
        body = mock_client.request.call_args.kwargs["json"]
        assert body["name"] == "Oil Change"
        assert body["note"] == "Customer waiting"
        assert body["technicianId"] == "tech-7"
        assert body["orderId"] == "ord-3"
        assert body["color"] == "green"
        assert body["locationId"] == "loc-9"
        await client.close()

    @pytest.mark.asyncio
    async def test_returns_raw_body_when_api_omits_data_envelope(self):
        """Some Shopmonkey writes answer with the bare record. Returning None
        here would lose the appointment id we hand back as confirmation."""
        client = ShopmonkeyClient(api_token="test-token")
        bare = MagicMock(status_code=200)
        bare.json.return_value = {"id": "appt-bare"}
        bare.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=bare)
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.create_appointment(
                customer_id="c", vehicle_id="v", start_date="s", end_date="e"
            )
        assert result == {"id": "appt-bare"}
        await client.close()


class TestGetAppointment:
    @pytest.mark.asyncio
    async def test_returns_unwrapped_appointment(self):
        client = ShopmonkeyClient(api_token="test-token")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_ok({"id": "appt-1", "name": "Oil Change"}))
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.get_appointment("appt-1")
        assert result == {"id": "appt-1", "name": "Oil Change"}
        assert mock_client.request.call_args.kwargs["url"] == "/v3/appointment/appt-1"
        await client.close()

    @pytest.mark.asyncio
    async def test_missing_appointment_is_none_not_an_error(self):
        """Lookup of a cancelled/purged appointment is an expected miss."""
        client = ShopmonkeyClient(api_token="test-token")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_err(404))
        with patch.object(client, "_get_client", return_value=mock_client):
            assert await client.get_appointment("gone") is None
        await client.close()

    @pytest.mark.asyncio
    async def test_server_error_propagates(self):
        """A 500 must not be flattened into "not found" - that would let the
        caller conclude a slot is free when we simply couldn't check."""
        client = ShopmonkeyClient(api_token="test-token")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_err(500))
        with patch.object(client, "_get_client", return_value=mock_client):
            with pytest.raises(ShopmonkeyAPIError) as exc:
                await client.get_appointment("appt-1")
        assert exc.value.status_code == 500
        await client.close()


class TestDeleteAppointment:
    """Cancellation. Reporting success for a delete that didn't happen leaves
    a ghost booking blocking a tech's calendar."""

    @pytest.mark.asyncio
    async def test_deletes_with_an_empty_json_body(self):
        """The API rejects a DELETE that has a Content-Type header but no body,
        so `{}` must be sent explicitly."""
        client = ShopmonkeyClient(api_token="test-token")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_ok({}))
        with patch.object(client, "_get_client", return_value=mock_client):
            assert await client.delete_appointment("appt-1") is True
        call = mock_client.request.call_args
        assert call.kwargs["method"] == "DELETE"
        assert call.kwargs["url"] == "/v3/appointment/appt-1"
        assert call.kwargs["json"] == {}
        await client.close()

    @pytest.mark.asyncio
    async def test_already_gone_reports_false(self):
        """404 means nothing was deleted - the caller must not claim it was."""
        client = ShopmonkeyClient(api_token="test-token")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_err(404))
        with patch.object(client, "_get_client", return_value=mock_client):
            assert await client.delete_appointment("appt-1") is False
        await client.close()

    @pytest.mark.asyncio
    async def test_forbidden_reports_false(self):
        """A token without delete scope leaves the appointment on the calendar;
        surfacing that as False lets the caller tell the customer to phone in."""
        client = ShopmonkeyClient(api_token="test-token")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_err(403))
        with patch.object(client, "_get_client", return_value=mock_client):
            assert await client.delete_appointment("appt-1") is False
        await client.close()

    @pytest.mark.asyncio
    async def test_server_error_propagates(self):
        """An unexpected failure must surface, not be swallowed as "not
        deleted" - the appointment's real state is unknown."""
        client = ShopmonkeyClient(api_token="test-token")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_err(500))
        with patch.object(client, "_get_client", return_value=mock_client):
            with pytest.raises(ShopmonkeyAPIError) as exc:
                await client.delete_appointment("appt-1")
        assert exc.value.status_code == 500
        await client.close()


class TestWorkflowStatuses:
    """The order has to land in the "Scheduled" column or it never shows up on
    the shop's board."""

    @pytest.mark.asyncio
    async def test_resolves_status_id_by_name(self):
        client = ShopmonkeyClient(api_token="test-token")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            return_value=_ok(
                [
                    {"id": "ws-est", "name": "Estimate"},
                    {"id": "ws-sched", "name": "Scheduled"},
                ]
            )
        )
        with patch.object(client, "_get_client", return_value=mock_client):
            assert await client.get_workflow_status_id("Scheduled") == "ws-sched"
        await client.close()

    @pytest.mark.asyncio
    async def test_unknown_name_returns_none_rather_than_a_wrong_column(self):
        client = ShopmonkeyClient(api_token="test-token")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_ok([{"id": "ws-est", "name": "Estimate"}]))
        with patch.object(client, "_get_client", return_value=mock_client):
            assert await client.get_workflow_status_id("Scheduled") is None
        await client.close()

    @pytest.mark.asyncio
    async def test_location_id_scopes_the_status_list(self):
        """Workflow columns are per-location; the wrong shop's ids are invalid."""
        client = ShopmonkeyClient(api_token="test-token", location_id="loc-9")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_ok([]))
        with patch.object(client, "_get_client", return_value=mock_client):
            assert await client.get_workflow_statuses() == []
        assert mock_client.request.call_args.kwargs["params"]["locationId"] == "loc-9"
        await client.close()


class TestCreateOrder:
    @pytest.mark.asyncio
    async def test_sends_workflow_status_and_default_status(self):
        client = ShopmonkeyClient(api_token="test-token", location_id="loc-9")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_ok({"id": "ord-1"}))
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.create_order(
                customer_id="cust-1", vehicle_id="veh-1", workflow_status_id="ws-sched"
            )
        assert result == {"id": "ord-1"}
        call = mock_client.request.call_args
        assert call.kwargs["method"] == "POST"
        assert call.kwargs["url"] == "/v3/order"
        assert call.kwargs["json"] == {
            "customerId": "cust-1",
            "vehicleId": "veh-1",
            "workflowStatusId": "ws-sched",
            "status": "Estimate",
            "locationId": "loc-9",
        }
        await client.close()

    @pytest.mark.asyncio
    async def test_optional_color_and_name_are_omitted_when_unset(self):
        """Nulls in the body are rejected by the order endpoint."""
        client = ShopmonkeyClient(api_token="test-token")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_ok({"id": "ord-1"}))
        with patch.object(client, "_get_client", return_value=mock_client):
            await client.create_order(
                customer_id="c", vehicle_id="v", workflow_status_id="ws", status="Invoice"
            )
        body = mock_client.request.call_args.kwargs["json"]
        assert "color" not in body
        assert "name" not in body
        assert body["status"] == "Invoice"
        await client.close()

    @pytest.mark.asyncio
    async def test_includes_color_and_name_when_given(self):
        client = ShopmonkeyClient(api_token="test-token")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_ok({"id": "ord-1"}))
        with patch.object(client, "_get_client", return_value=mock_client):
            await client.create_order(
                customer_id="c",
                vehicle_id="v",
                workflow_status_id="ws",
                color="blue",
                name="Online Booking",
            )
        body = mock_client.request.call_args.kwargs["json"]
        assert body["color"] == "blue"
        assert body["name"] == "Online Booking"
        await client.close()


class TestAttachServicesToOrder:
    @pytest.mark.asyncio
    async def test_posts_the_service_list_verbatim_to_the_order(self):
        """The endpoint takes a bare JSON array; wrapping it in an object (or
        dropping `labors`) leaves the order priced at $0."""
        client = ShopmonkeyClient(api_token="test-token")
        services = [
            {
                "cannedServiceId": "svc-1",
                "name": "Oil Change",
                "labors": [{"name": "Labor", "hours": 1.0}],
            }
        ]
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_ok({"services": [{"id": "os-1"}]}))
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.attach_services_to_order("ord-1", services)
        assert result == [{"id": "os-1"}]
        call = mock_client.request.call_args
        assert call.kwargs["method"] == "POST"
        assert call.kwargs["url"] == "/v3/order/ord-1/service"
        assert call.kwargs["json"] == services
        await client.close()

    @pytest.mark.asyncio
    async def test_accepts_a_bare_list_response(self):
        """The endpoint has been seen answering with the array directly."""
        client = ShopmonkeyClient(api_token="test-token")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_ok([{"id": "os-1"}, {"id": "os-2"}]))
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.attach_services_to_order("ord-1", [{"name": "x"}])
        assert result == [{"id": "os-1"}, {"id": "os-2"}]
        await client.close()

    @pytest.mark.asyncio
    async def test_missing_services_key_yields_empty_list(self):
        """A response body with no `services` key means nothing was attached,
        so the reported attachment list is empty."""
        client = ShopmonkeyClient(api_token="test-token")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_ok({"id": "ord-1"}))
        with patch.object(client, "_get_client", return_value=mock_client):
            assert await client.attach_services_to_order("ord-1", [{"name": "x"}]) == []
        await client.close()


class TestActiveUserIds:
    """Deactivated technicians must never be offered a slot, and the lookup is
    cached so a page of availability checks doesn't hammer /v3/user."""

    _USERS = [
        {"id": "u-active", "active": True},
        {"id": "u-inactive", "active": False},
        {"id": "u-no-flag"},
        {"active": True},  # no id
    ]

    @pytest.mark.asyncio
    async def test_only_active_users_with_ids_are_returned(self):
        client = ShopmonkeyClient(api_token="test-token")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_ok(self._USERS))
        with patch.object(client, "_get_client", return_value=mock_client):
            assert await client.get_active_user_ids() == {"u-active"}
        await client.close()

    @pytest.mark.asyncio
    async def test_second_call_is_served_from_cache(self):
        client = ShopmonkeyClient(api_token="test-token")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_ok(self._USERS))
        with patch.object(client, "_get_client", return_value=mock_client):
            await client.get_active_user_ids()
            await client.get_active_user_ids()
        assert mock_client.request.call_count == 1
        await client.close()

    @pytest.mark.asyncio
    async def test_expired_cache_refetches_and_reflects_changes(self):
        """A tech deactivated mid-day has to drop out of availability once the
        TTL lapses, otherwise the shop keeps booking them."""
        client = ShopmonkeyClient(api_token="test-token")
        client._active_user_ids_cache_ttl = -1.0  # already expired on write
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            side_effect=[
                _ok([{"id": "u-active", "active": True}]),
                _ok([{"id": "u-active", "active": False}]),
            ]
        )
        with patch.object(client, "_get_client", return_value=mock_client):
            assert await client.get_active_user_ids() == {"u-active"}
            assert await client.get_active_user_ids() == set()
        assert mock_client.request.call_count == 2
        await client.close()

    @pytest.mark.asyncio
    async def test_get_users_scopes_to_location(self):
        client = ShopmonkeyClient(api_token="test-token", location_id="loc-9")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_ok([{"id": "u-1"}]))
        with patch.object(client, "_get_client", return_value=mock_client):
            assert await client.get_users() == [{"id": "u-1"}]
        call = mock_client.request.call_args
        assert call.kwargs["url"] == "/v3/user"
        assert call.kwargs["params"]["locationId"] == "loc-9"
        await client.close()


class TestGetAppointmentsForDateExtras:
    @pytest.mark.asyncio
    async def test_scopes_the_day_query_to_the_location(self):
        """Without this, a multi-location account counts another shop's
        appointments as conflicts and starves this shop's availability."""
        client = ShopmonkeyClient(api_token="test-token", location_id="loc-9")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_ok([], meta={"hasMore": False}))
        with patch.object(client, "_get_client", return_value=mock_client):
            await client.get_appointments_for_date("2026-05-27")
        assert mock_client.request.call_args.kwargs["params"]["locationId"] == "loc-9"
        await client.close()

    @pytest.mark.asyncio
    async def test_window_spans_the_whole_requested_day(self):
        """The window IS this function's job. A short end bound (e.g. noon)
        hides every afternoon appointment, and the availability engine then
        double-books the afternoon."""
        client = ShopmonkeyClient(api_token="test-token")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_ok([], meta={"hasMore": False}))
        with patch.object(client, "_get_client", return_value=mock_client):
            await client.get_appointments_for_date("2026-05-27")
        params = mock_client.request.call_args.kwargs["params"]
        where = json.loads(params["where"])
        assert where["startDate"] == {
            "gte": "2026-05-27T00:00:00Z",
            "lt": "2026-05-27T23:59:59Z",
        }
        await client.close()

    @pytest.mark.asyncio
    async def test_truncated_page_warns_and_still_returns_the_rows_it_got(self):
        """Overflowing the 100-row cap is worth a warning, but discarding or
        raising would blank out conflict detection for the whole day.

        The warning is the branch's entire contract - it is the only signal
        that a day silently lost appointments and needs real pagination.
        """
        client = ShopmonkeyClient(api_token="test-token")
        rows = [{"id": f"appt-{i}"} for i in range(3)]
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            return_value=_ok(rows, meta={"hasMore": True, "total": 137})
        )
        with patch.object(client, "_get_client", return_value=mock_client):
            with patch("shopmonkey_client.logger.warning") as warn:
                result = await client.get_appointments_for_date("2026-05-27")
        assert warn.call_count == 1
        assert warn.call_args.args[0] == "appointment_list_has_more"
        assert warn.call_args.kwargs["total"] == 137
        assert warn.call_args.kwargs["date"] == "2026-05-27"
        assert warn.call_args.kwargs["returned"] == 3
        # Secondary: the truncated page is still handed back, not dropped.
        assert result == rows
        await client.close()

    @pytest.mark.asyncio
    async def test_missing_meta_block_is_tolerated(self):
        """Not every deployment returns `meta`; a KeyError here would break
        every availability check."""
        client = ShopmonkeyClient(api_token="test-token")
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_ok([{"id": "appt-1"}]))
        with patch.object(client, "_get_client", return_value=mock_client):
            assert await client.get_appointments_for_date("2026-05-27") == [{"id": "appt-1"}]
        await client.close()


class TestCustomerPhoneMatchingRequiresAFullNumber:
    """Regression: `entry_digits.endswith(target_digits[-10:])` had no minimum
    length, so a short phone matched on suffix alone and a digit-free phone
    matched ANY customer holding any number at all. Both silently attach a
    booking to the wrong person - the misattribution class this function was
    rewritten to prevent.
    """

    def _customer(self, number, email=None):
        return {
            "id": "cust-existing",
            "firstName": "Jane",
            "lastName": "Doe",
            "emails": [{"email": email}] if email else [],
            "phoneNumbers": [{"number": number}],
        }

    def test_seven_digit_local_number_does_not_match_a_different_number(self):
        """validate_phone accepts 7 digits, so "555-1234" reaches here. It must
        not suffix-match a different person's +19995551234."""
        assert (
            ShopmonkeyClient._customer_matches(self._customer("+19995551234"), None, "555-1234")
            is False
        )

    def test_phone_with_no_digits_matches_nobody(self):
        """ "".endswith("") is True, which previously matched every customer."""
        assert (
            ShopmonkeyClient._customer_matches(self._customer("+19998887777"), None, "call my cell")
            is False
        )

    def test_short_number_still_matches_itself_exactly(self):
        """Below 10 digits we require exact equality rather than no match at
        all, so a shop that stores extensions still resolves them."""
        assert ShopmonkeyClient._customer_matches(self._customer("5551234"), None, "555-1234")

    def test_full_national_number_still_matches_across_formats(self):
        """The whole point of suffix matching: +1/dashes/bare are one person."""
        cust = self._customer("+18165551234")
        for written in ("+1 816 555 1234", "816-555-1234", "8165551234", "(816) 555-1234"):
            assert ShopmonkeyClient._customer_matches(cust, None, written), written

    def test_different_full_numbers_still_do_not_match(self):
        assert (
            ShopmonkeyClient._customer_matches(self._customer("+18165551234"), None, "816-555-9999")
            is False
        )
