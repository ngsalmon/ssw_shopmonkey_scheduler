"""Unit tests for FastAPI endpoints using TestClient."""

import os
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def mock_shopmonkey_client():
    """Create a mock ShopmonkeyClient."""
    client = AsyncMock()
    client.get_bookable_canned_services = AsyncMock(
        return_value=[
            {
                "id": "svc-1",
                "name": "Window Tint",
                "totalCents": 15000,
                "labels": [{"name": "Window Tint"}],
                "labors": [{"hours": 2.5}],
            },
            {
                "id": "svc-2",
                "name": "Paint Protection Film",
                "totalCents": 50000,
                "labels": [{"name": "Vinyl"}],
                "labors": [{"hours": 8}, {"hours": 1}],
            },
        ]
    )
    client.get_canned_service = AsyncMock(
        return_value={
            "id": "svc-1",
            "name": "Window Tint",
            "totalCents": 15000,
            "labels": [{"name": "Window Tint"}],
            "estimatedDuration": 60,
        }
    )
    client.get_appointments_for_date = AsyncMock(return_value=[])
    client.get_busy_techs_for_appointments = AsyncMock(return_value={})
    client.find_or_create_customer = AsyncMock(return_value={"id": "cust-123"})
    client.find_or_create_vehicle = AsyncMock(return_value={"id": "veh-456"})
    client.create_appointment = AsyncMock(return_value={"id": "appt-789"})
    client.health_check = AsyncMock(return_value=True)
    client.close = AsyncMock()
    return client


@pytest.fixture
def mock_sheets_client():
    """Create a mock SheetsClient with async methods."""
    client = MagicMock()
    # Mock async methods
    client.get_techs_for_department = AsyncMock(
        return_value=[
            {"tech_id": "tech-1", "tech_name": "John Doe", "priority": 1},
            {"tech_id": "tech-2", "tech_name": "Jane Smith", "priority": 2},
        ]
    )
    client.get_all_departments = AsyncMock(return_value=["Window Tint", "Vinyl", "Detail"])
    # No concurrency cap by default (matches an empty MAX CONCURRENCY row).
    client.get_max_concurrency_for_department = AsyncMock(return_value=None)
    client.get_tech_departments = AsyncMock(
        return_value={
            "tech-1": {"tech_name": "John Doe", "departments": {"Window Tint": 1}},
            "tech-2": {"tech_name": "Jane Smith", "departments": {"Vinyl": 1}},
        }
    )
    client.health_check = AsyncMock(return_value=True)
    client.get_cache_status = MagicMock(
        return_value={
            "cache_size": 0,
            "cache_ttl_seconds": 300,
            "cache_maxsize": 100,
        }
    )
    return client


@pytest.fixture
def mock_config():
    """Create a mock configuration."""
    return {
        "business_hours": {
            "monday": {"open": "09:00", "close": "17:00"},
            "tuesday": {"open": "09:00", "close": "17:00"},
            "wednesday": {"open": "09:00", "close": "17:00"},
            "thursday": {"open": "09:00", "close": "17:00"},
            "friday": {"open": "09:00", "close": "17:00"},
        },
        "default_slot_duration_minutes": 60,
    }


@pytest.fixture
def test_client(mock_shopmonkey_client, mock_sheets_client, mock_config):
    """Create a TestClient with mocked dependencies."""
    # Clear any existing API_KEY environment variable for tests
    with patch.dict(os.environ, {"API_KEY": "", "ALLOWED_ORIGINS": ""}, clear=False):
        with (
            patch("main.ShopmonkeyClient", return_value=mock_shopmonkey_client),
            patch("main.SheetsClient", return_value=mock_sheets_client),
            patch("main.load_config", return_value=mock_config),
            patch("main.validate_config"),
            # Freeze "now" well before every fixture date so the elapsed-slot
            # guard is a no-op for existing tests (they use 2026-01-15+ dates).
            # Elapsed-slot behavior is exercised by dedicated tests that
            # re-patch main._now_local.
            patch("main._now_local", return_value=datetime(2026, 1, 1, 0, 0, 0)),
        ):
            from main import app

            with TestClient(app) as client:
                yield client


@pytest.fixture
def test_client_with_api_key(mock_shopmonkey_client, mock_sheets_client, mock_config):
    """Create a TestClient with API key authentication enabled."""
    with patch.dict(
        os.environ, {"API_KEY": "test-api-key-123", "ALLOWED_ORIGINS": ""}, clear=False
    ):
        with (
            patch("main.ShopmonkeyClient", return_value=mock_shopmonkey_client),
            patch("main.SheetsClient", return_value=mock_sheets_client),
            patch("main.load_config", return_value=mock_config),
            patch("main.validate_config"),
            patch("main.API_KEY", "test-api-key-123"),
            patch("main._now_local", return_value=datetime(2026, 1, 1, 0, 0, 0)),
        ):
            from main import app

            with TestClient(app) as client:
                yield client


@pytest.fixture
def main_module():
    """The imported `main` module, for testing its pure helpers directly."""
    import main

    return main


@pytest.fixture
def clean_round_robin(main_module):
    """Isolate the process-global round-robin state between tests."""
    main_module.round_robin_tracker.clear()
    yield main_module.round_robin_tracker
    main_module.round_robin_tracker.clear()


@pytest.fixture
def client_factory(mock_shopmonkey_client, mock_sheets_client):
    """Build a TestClient over an arbitrary config, reusing the client mocks.

    Same wiring as `test_client`, but the config is a parameter so tests can
    exercise config-driven behavior (disabled departments, order creation)
    without duplicating the patch stack.
    """

    @contextmanager
    def _make(config):
        with patch.dict(os.environ, {"API_KEY": "", "ALLOWED_ORIGINS": ""}, clear=False):
            with (
                patch("main.ShopmonkeyClient", return_value=mock_shopmonkey_client),
                patch("main.SheetsClient", return_value=mock_sheets_client),
                patch("main.load_config", return_value=config),
                patch("main.validate_config"),
                patch("main._now_local", return_value=datetime(2026, 1, 1, 0, 0, 0)),
            ):
                from main import app

                with TestClient(app) as client:
                    yield client

    return _make


class TestHealthEndpoint:
    """Tests for /health endpoint."""

    def test_health_returns_healthy(self, test_client):
        """Should return healthy status."""
        response = test_client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}

    def test_liveness_returns_healthy(self, test_client):
        """Should return healthy status for liveness probe."""
        response = test_client.get("/health/live")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}

    def test_readiness_returns_status(self, test_client):
        """Should return detailed readiness status."""
        response = test_client.get("/health/ready")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "shopmonkey" in data
        assert "sheets" in data


class TestServicesEndpoint:
    """Tests for /services endpoint."""

    def test_returns_list_of_services(self, test_client):
        """Should return list of bookable services."""
        response = test_client.get("/services")
        assert response.status_code == 200
        data = response.json()
        assert "services" in data
        assert len(data["services"]) == 2
        assert data["services"][0]["id"] == "svc-1"
        assert data["services"][0]["name"] == "Window Tint"

    def test_services_include_price(self, test_client):
        """Should include price in cents."""
        response = test_client.get("/services")
        data = response.json()
        assert data["services"][0]["totalCents"] == 15000

    def test_services_include_labor_hours(self, test_client):
        """Should include labor hours (sum of all labor entries)."""
        response = test_client.get("/services")
        data = response.json()
        # svc-1 has one labor entry of 2.5 hours
        assert data["services"][0]["laborHours"] == 2.5
        # svc-2 has two labor entries: 8 + 1 = 9 hours
        assert data["services"][1]["laborHours"] == 9.0


class TestAvailabilityEndpoint:
    """Tests for /availability endpoint."""

    def test_returns_available_slots(self, test_client):
        """Should return available time slots."""
        response = test_client.get("/availability?service_id=svc-1&date=2026-01-19")
        assert response.status_code == 200
        data = response.json()
        assert data["service_id"] == "svc-1"
        assert data["date"] == "2026-01-19"
        assert "slots" in data

    def test_drops_slots_that_have_already_started_today(self, test_client):
        """Slots whose start time is in the past for today are not offered."""
        # It's noon on the requested day; morning slots have elapsed.
        with patch("main._now_local", return_value=datetime(2026, 1, 19, 12, 0, 0)):
            response = test_client.get("/availability?service_id=svc-1&date=2026-01-19")
        assert response.status_code == 200
        starts = [s["start"] for s in response.json()["slots"]]
        assert starts, "expected some afternoon slots to remain"
        assert "09:00" not in starts
        assert all(s > "12:00" for s in starts)

    def _ooo_block(self, appt_id, name):
        return {
            "id": appt_id,
            "orderId": None,  # time off, not a work order
            "startDate": "2026-01-19T09:00:00-06:00",
            "endDate": "2026-01-19T10:00:00-06:00",
            "name": name,
        }

    def test_time_off_reduces_advertised_techs(self, test_client, mock_shopmonkey_client):
        """One of two qualified techs is out on a ticketless block: the 09:00
        slot stays open but advertises one fewer tech."""
        mock_shopmonkey_client.get_appointments_for_date = AsyncMock(
            return_value=[self._ooo_block("appt-ooo-1", "John Out")]
        )
        mock_shopmonkey_client.get_busy_techs_for_appointments = AsyncMock(
            return_value={"appt-ooo-1": {"tech-1"}}
        )
        response = test_client.get("/availability?service_id=svc-1&date=2026-01-19")
        assert response.status_code == 200
        slots = {s["start"]: s for s in response.json()["slots"]}
        assert slots["09:00"]["available_techs"] == 1
        # A later slot the block doesn't touch keeps both techs.
        assert slots["11:00"]["available_techs"] == 2

    def test_slot_dropped_when_all_techs_are_out(self, test_client, mock_shopmonkey_client):
        """The reported bug, through the real endpoint: with every qualified
        tech out, the slot must not be offered at all."""
        mock_shopmonkey_client.get_appointments_for_date = AsyncMock(
            return_value=[
                self._ooo_block("appt-ooo-1", "John Out"),
                self._ooo_block("appt-ooo-2", "Jane Out"),
            ]
        )
        mock_shopmonkey_client.get_busy_techs_for_appointments = AsyncMock(
            return_value={"appt-ooo-1": {"tech-1"}, "appt-ooo-2": {"tech-2"}}
        )
        response = test_client.get("/availability?service_id=svc-1&date=2026-01-19")
        assert response.status_code == 200
        starts = [s["start"] for s in response.json()["slots"]]
        assert "09:00" not in starts
        assert starts, "later slots should still be offered"

    def test_shop_wide_block_naming_no_tech_is_ignored(self, test_client, mock_shopmonkey_client):
        """A ticketless entry naming nobody ("Cars & Coffee") blocks no one -
        shop-wide closures are deliberately out of scope."""
        mock_shopmonkey_client.get_appointments_for_date = AsyncMock(
            return_value=[self._ooo_block("appt-event", "Cars & Coffee")]
        )
        mock_shopmonkey_client.get_busy_techs_for_appointments = AsyncMock(
            return_value={"appt-event": set()}
        )
        response = test_client.get("/availability?service_id=svc-1&date=2026-01-19")
        assert response.status_code == 200
        slots = {s["start"]: s for s in response.json()["slots"]}
        assert slots["09:00"]["available_techs"] == 2

    def test_invalid_date_format_returns_400(self, test_client):
        """Should return 400 for invalid date format."""
        response = test_client.get("/availability?service_id=svc-1&date=invalid")
        assert response.status_code == 400
        assert "Invalid date format" in response.json()["detail"]

    def test_missing_service_id_returns_422(self, test_client):
        """Should return 422 when service_id is missing."""
        response = test_client.get("/availability?date=2026-01-19")
        assert response.status_code == 422

    def test_missing_date_returns_422(self, test_client):
        """Should return 422 when date is missing."""
        response = test_client.get("/availability?service_id=svc-1")
        assert response.status_code == 422

    def test_service_not_found_returns_404(self, test_client, mock_shopmonkey_client):
        """Should return 404 when service not found."""
        mock_shopmonkey_client.get_canned_service = AsyncMock(return_value=None)
        response = test_client.get("/availability?service_id=unknown&date=2026-01-19")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    def test_service_without_label_returns_404(self, test_client, mock_shopmonkey_client):
        """Should return 404 when service has no label."""
        mock_shopmonkey_client.get_canned_service = AsyncMock(
            return_value={
                "id": "svc-no-label",
                "name": "Unlabeled Service",
                "labels": [],
            }
        )
        response = test_client.get("/availability?service_id=svc-no-label&date=2026-01-19")
        assert response.status_code == 404

    def test_no_techs_for_department_returns_404(self, test_client, mock_sheets_client):
        """Should return 404 when no techs for department."""
        mock_sheets_client.get_techs_for_department = AsyncMock(return_value=[])
        response = test_client.get("/availability?service_id=svc-1&date=2026-01-19")
        assert response.status_code == 404


class TestBookEndpoint:
    """Tests for /book endpoint."""

    def test_successful_booking(self, test_client):
        """Should successfully book appointment."""
        booking_request = {
            "service_id": "svc-1",
            "slot_start": "2026-01-19T09:00:00",
            "slot_end": "2026-01-19T10:00:00",
            "customer": {
                "firstName": "Test",
                "lastName": "Customer",
                "email": "test@example.com",
                "phone": "555-1234",
            },
            "vehicle": {
                "year": 2022,
                "make": "Toyota",
                "model": "Camry",
            },
        }
        response = test_client.post("/book", json=booking_request)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "appointment_id" in data
        assert "confirmation_number" in data
        assert data["confirmation_number"].startswith("SM-")

    def test_rejects_booking_for_elapsed_slot(self, test_client, mock_shopmonkey_client):
        """A slot whose start is already in the past is rejected with 409,
        before any customer/vehicle/appointment work happens."""
        booking_request = {
            "service_id": "svc-1",
            "slot_start": "2026-01-19T09:00:00",
            "slot_end": "2026-01-19T10:00:00",
            "customer": {"firstName": "Test", "lastName": "Customer"},
            "vehicle": {"year": 2022, "make": "Toyota", "model": "Camry"},
        }
        # Now is noon on the same day -> the 9:00 AM slot has elapsed.
        with patch("main._now_local", return_value=datetime(2026, 1, 19, 12, 0, 0)):
            response = test_client.post("/book", json=booking_request)
        assert response.status_code == 409
        assert "already passed" in response.json()["detail"].lower()
        # Fail-fast: no booking side effects occurred.
        mock_shopmonkey_client.create_appointment.assert_not_called()

    def test_booking_creates_customer(self, test_client, mock_shopmonkey_client):
        """Should call find_or_create_customer with correct data."""
        booking_request = {
            "service_id": "svc-1",
            "slot_start": "2026-01-19T09:00:00",
            "slot_end": "2026-01-19T10:00:00",
            "customer": {
                "firstName": "John",
                "lastName": "Doe",
                "email": "john@example.com",
                "phone": "555-1234",
            },
            "vehicle": {
                "year": 2022,
                "make": "Toyota",
                "model": "Camry",
            },
        }
        test_client.post("/book", json=booking_request)
        mock_shopmonkey_client.find_or_create_customer.assert_called_once_with(
            first_name="John",
            last_name="Doe",
            email="john@example.com",
            phone="555-1234",
        )

    def test_booking_creates_vehicle(self, test_client, mock_shopmonkey_client):
        """Should call find_or_create_vehicle with correct data."""
        booking_request = {
            "service_id": "svc-1",
            "slot_start": "2026-01-19T09:00:00",
            "slot_end": "2026-01-19T10:00:00",
            "customer": {
                "firstName": "John",
                "lastName": "Doe",
            },
            "vehicle": {
                "year": 2022,
                "make": "Honda",
                "model": "Civic",
                "vin": "1HGBH41JXMN109186",
            },
        }
        test_client.post("/book", json=booking_request)
        mock_shopmonkey_client.find_or_create_vehicle.assert_called_once_with(
            customer_id="cust-123",
            year=2022,
            make="Honda",
            model="Civic",
            vin="1HGBH41JXMN109186",
        )

    def test_booking_notes_flag_customer_name_mismatch(self, test_client, mock_shopmonkey_client):
        """Surface existing-record name when find_or_create reuses a customer.

        Regression: Anne saw a booking appear under the wrong name because
        find_or_create_customer matched on email and reused the older record.
        The note should expose both names so staff can confirm.
        """
        mock_shopmonkey_client.find_or_create_customer = AsyncMock(
            return_value={
                "id": "cust-existing",
                "firstName": "Jena",
                "lastName": "Scaletty",
                "email": "shared@example.com",
            }
        )
        booking_request = {
            "service_id": "svc-1",
            "slot_start": "2026-01-19T09:00:00",
            "slot_end": "2026-01-19T10:00:00",
            "customer": {
                "firstName": "TJ",
                "lastName": "McLaughlin",
                "email": "shared@example.com",
            },
            "vehicle": {"year": 2022, "make": "Toyota", "model": "Camry"},
        }
        response = test_client.post("/book", json=booking_request)
        assert response.status_code == 200

        mock_shopmonkey_client.create_appointment.assert_called_once()
        notes = mock_shopmonkey_client.create_appointment.call_args.kwargs.get("notes", "")
        assert "Customer record on file: Jena Scaletty" in notes
        assert "TJ McLaughlin" in notes

    def test_booking_creates_order_and_attaches_service_when_flag_on(
        self, mock_shopmonkey_client, mock_sheets_client
    ):
        """OOTB parity: with online_booking.create_order=true the /book flow
        also POSTs an order, attaches the canned service as a line item, and
        sets orderId on the appointment.
        """
        mock_shopmonkey_client.get_workflow_status_id = AsyncMock(return_value="ws_scheduled")
        mock_shopmonkey_client.create_order = AsyncMock(
            return_value={"id": "order_42", "number": "8000"}
        )
        mock_shopmonkey_client.attach_services_to_order = AsyncMock(return_value=[])
        # Use the existing canned service mock; add labors so we can assert
        # forwarding.
        mock_shopmonkey_client.get_canned_service = AsyncMock(
            return_value={
                "id": "svc-1",
                "name": "Window Tint",
                "totalCents": 15000,
                "labels": [{"name": "Window Tint"}],
                "labors": [{"name": "Tint Labor", "hours": 1.5, "rateCents": 13000}],
            }
        )
        config_with_flag = {
            "business_hours": {
                "monday": {"open": "09:00", "close": "17:00"},
                "tuesday": {"open": "09:00", "close": "17:00"},
                "wednesday": {"open": "09:00", "close": "17:00"},
                "thursday": {"open": "09:00", "close": "17:00"},
                "friday": {"open": "09:00", "close": "17:00"},
            },
            "default_slot_duration_minutes": 60,
            "online_booking": {
                "create_order": True,
                "workflow_status_name": "Scheduled",
                "order_status": "Estimate",
                "order_color": "blue",
            },
        }
        with patch.dict(os.environ, {"API_KEY": "", "ALLOWED_ORIGINS": ""}, clear=False):
            with (
                patch("main.ShopmonkeyClient", return_value=mock_shopmonkey_client),
                patch("main.SheetsClient", return_value=mock_sheets_client),
                patch("main.load_config", return_value=config_with_flag),
                patch("main.validate_config"),
                patch("main._now_local", return_value=datetime(2026, 1, 1, 0, 0, 0)),
            ):
                from main import app

                with TestClient(app) as client:
                    response = client.post(
                        "/book",
                        json={
                            "service_id": "svc-1",
                            "slot_start": "2026-01-19T09:00:00",
                            "slot_end": "2026-01-19T10:30:00",
                            "customer": {"firstName": "Russ", "lastName": "Nguyen"},
                            "vehicle": {"year": 2016, "make": "Toyota", "model": "RAV4"},
                        },
                    )

        assert response.status_code == 200, response.text
        mock_shopmonkey_client.get_workflow_status_id.assert_awaited_once_with("Scheduled")
        mock_shopmonkey_client.create_order.assert_awaited_once()
        order_kwargs = mock_shopmonkey_client.create_order.call_args.kwargs
        assert order_kwargs["workflow_status_id"] == "ws_scheduled"
        assert order_kwargs["status"] == "Estimate"
        assert order_kwargs["color"] == "blue"
        assert order_kwargs["name"] == "Russ N. / 2016 Toyota RAV4 / Window Tint"

        mock_shopmonkey_client.attach_services_to_order.assert_awaited_once()
        attach_kwargs = mock_shopmonkey_client.attach_services_to_order.call_args.kwargs
        assert attach_kwargs["order_id"] == "order_42"
        services = attach_kwargs["services"]
        assert len(services) == 1
        assert services[0]["cannedServiceId"] == "svc-1"
        assert services[0]["name"] == "Window Tint"
        # Labor is stamped with the assigned tech so the next /availability
        # check sees this booking as taking that tech (instead of falling
        # into the "unattributed" bucket).
        assert services[0]["labors"] == [
            {
                "name": "Tint Labor",
                "hours": 1.5,
                "rateCents": 13000,
                "technicianId": "tech-1",
            }
        ]

        appt_kwargs = mock_shopmonkey_client.create_appointment.call_args.kwargs
        assert appt_kwargs["order_id"] == "order_42"
        assert appt_kwargs["title"] == "Russ N. / 2016 Toyota RAV4 / Window Tint"

    def test_booking_attach_payload_forwards_parts_and_rate_id(
        self, mock_shopmonkey_client, mock_sheets_client
    ):
        """Regression for Anne's 2026-05-20 report: attach payload was
        missing parts entirely and stripped labor rateId, so tickets
        showed the shop default rate ($130/hr) instead of the canned
        service's rate ($100/hr) and had no parts. Verify both are
        forwarded so the ticket total matches what the customer saw.
        """
        mock_shopmonkey_client.get_workflow_status_id = AsyncMock(return_value="ws_sched")
        mock_shopmonkey_client.create_order = AsyncMock(
            return_value={"id": "order_99", "number": "9000"}
        )
        mock_shopmonkey_client.attach_services_to_order = AsyncMock(return_value=[])
        # Mirrors the live Window Tint Ceramic canned service that produced
        # the wrong ticket on 2026-05-20 (id stripped for the test).
        mock_shopmonkey_client.get_canned_service = AsyncMock(
            return_value={
                "id": "svc-tint-ceramic",
                "name": "Window Tint - Full Sedan/Truck - Ceramic",
                "totalCents": 38047,
                "labels": [{"name": "Window Tint"}],
                "labors": [
                    {
                        "name": "Install window tint on side windows and rear glass",
                        "hours": 3.21,
                        "rateCents": 10000,
                        "rateId": "lr_window_tint_sedan_ceramic",
                        "taxable": False,
                    }
                ],
                "parts": [
                    {
                        "name": "Ceramic IR",
                        "quantity": 3.5,
                        "retailCostCents": 1543,
                        "wholesaleCostCents": 1189,
                        "partNumber": "CERAMICIR",
                        "taxable": True,
                    }
                ],
            }
        )
        config_with_flag = {
            "business_hours": {
                "monday": {"open": "09:00", "close": "17:00"},
                "tuesday": {"open": "09:00", "close": "17:00"},
                "wednesday": {"open": "09:00", "close": "17:00"},
                "thursday": {"open": "09:00", "close": "17:00"},
                "friday": {"open": "09:00", "close": "17:00"},
            },
            "default_slot_duration_minutes": 60,
            "online_booking": {
                "create_order": True,
                "workflow_status_name": "Scheduled",
                "order_status": "Estimate",
                "order_color": "blue",
            },
        }
        with patch.dict(os.environ, {"API_KEY": "", "ALLOWED_ORIGINS": ""}, clear=False):
            with (
                patch("main.ShopmonkeyClient", return_value=mock_shopmonkey_client),
                patch("main.SheetsClient", return_value=mock_sheets_client),
                patch("main.load_config", return_value=config_with_flag),
                patch("main.validate_config"),
                patch("main._now_local", return_value=datetime(2026, 1, 1, 0, 0, 0)),
            ):
                from main import app

                with TestClient(app) as client:
                    response = client.post(
                        "/book",
                        json={
                            "service_id": "svc-tint-ceramic",
                            "slot_start": "2026-05-27T13:00:00",
                            "slot_end": "2026-05-27T16:12:36",
                            "customer": {"firstName": "Anne", "lastName": "Wehner"},
                            "vehicle": {"year": 2022, "make": "Toyota", "model": "Prius"},
                        },
                    )

        assert response.status_code == 200, response.text
        attach_kwargs = mock_shopmonkey_client.attach_services_to_order.call_args.kwargs
        services = attach_kwargs["services"]
        assert len(services) == 1
        line = services[0]

        # Labor forwards rateId so Shopmonkey uses the canned rate, not the
        # shop default. Pre-fix this was missing.
        assert len(line["labors"]) == 1
        labor = line["labors"][0]
        assert labor["rateId"] == "lr_window_tint_sedan_ceramic"
        assert labor["rateCents"] == 10000
        assert labor["hours"] == 3.21
        assert labor["taxable"] is False
        # Labor is stamped with the assigned tech so per-tech availability
        # picks up our booking on the next /availability check.
        assert labor["technicianId"] in {"tech-1", "tech-2"}

        # Parts forwarded with quantity/retail/partNumber so the ticket has
        # line items instead of a labor-only $0-parts total. Pre-fix this
        # array was absent entirely.
        assert len(line["parts"]) == 1
        part = line["parts"][0]
        assert part["name"] == "Ceramic IR"
        assert part["quantity"] == 3.5
        assert part["retailCostCents"] == 1543
        assert part["partNumber"] == "CERAMICIR"
        assert part["taxable"] is True

    def test_booking_skips_order_creation_when_flag_off(self, test_client, mock_shopmonkey_client):
        """With online_booking absent from config the order endpoints are not called."""
        # The default test_client fixture's config has no online_booking block,
        # so booking should skip the order endpoints entirely.
        mock_shopmonkey_client.create_order = AsyncMock()
        mock_shopmonkey_client.attach_services_to_order = AsyncMock()
        response = test_client.post(
            "/book",
            json={
                "service_id": "svc-1",
                "slot_start": "2026-01-19T09:00:00",
                "slot_end": "2026-01-19T10:00:00",
                "customer": {"firstName": "Jane", "lastName": "Doe"},
                "vehicle": {"year": 2020, "make": "Honda", "model": "Civic"},
            },
        )
        assert response.status_code == 200
        mock_shopmonkey_client.create_order.assert_not_awaited()
        mock_shopmonkey_client.attach_services_to_order.assert_not_awaited()

    def test_booking_sends_dst_aware_iso_to_shopmonkey(self, test_client, mock_shopmonkey_client):
        """The booking ISO format must reflect DST: May -> -05:00, Jan -> -06:00."""
        # May slot - America/Chicago is CDT (-05:00) in May
        test_client.post(
            "/book",
            json={
                "service_id": "svc-1",
                "slot_start": "2026-05-20T11:00:00",
                "slot_end": "2026-05-20T12:00:00",
                "customer": {"firstName": "May", "lastName": "Tester"},
                "vehicle": {"year": 2024, "make": "Toyota", "model": "Camry"},
            },
        )
        may_kwargs = mock_shopmonkey_client.create_appointment.call_args.kwargs
        assert may_kwargs["start_date"] == "2026-05-20T11:00:00.000-05:00"
        assert may_kwargs["end_date"] == "2026-05-20T12:00:00.000-05:00"

        mock_shopmonkey_client.create_appointment.reset_mock()

        # January slot - CST (-06:00)
        test_client.post(
            "/book",
            json={
                "service_id": "svc-1",
                "slot_start": "2026-01-15T11:00:00",
                "slot_end": "2026-01-15T12:00:00",
                "customer": {"firstName": "Jan", "lastName": "Tester"},
                "vehicle": {"year": 2024, "make": "Toyota", "model": "Camry"},
            },
        )
        jan_kwargs = mock_shopmonkey_client.create_appointment.call_args.kwargs
        assert jan_kwargs["start_date"] == "2026-01-15T11:00:00.000-06:00"
        assert jan_kwargs["end_date"] == "2026-01-15T12:00:00.000-06:00"

    def test_booking_notes_omit_mismatch_when_names_match(
        self, test_client, mock_shopmonkey_client
    ):
        """No mismatch line when the returned customer matches the booking name."""
        mock_shopmonkey_client.find_or_create_customer = AsyncMock(
            return_value={
                "id": "cust-existing",
                "firstName": "TJ",
                "lastName": "McLaughlin",
            }
        )
        booking_request = {
            "service_id": "svc-1",
            "slot_start": "2026-01-19T09:00:00",
            "slot_end": "2026-01-19T10:00:00",
            "customer": {"firstName": "TJ", "lastName": "McLaughlin"},
            "vehicle": {"year": 2022, "make": "Toyota", "model": "Camry"},
        }
        response = test_client.post("/book", json=booking_request)
        assert response.status_code == 200

        notes = mock_shopmonkey_client.create_appointment.call_args.kwargs.get("notes", "")
        assert "Customer record on file" not in notes

    def test_booking_service_not_found(self, test_client, mock_shopmonkey_client):
        """Should return 404 when service not found."""
        mock_shopmonkey_client.get_canned_service = AsyncMock(return_value=None)
        booking_request = {
            "service_id": "unknown",
            "slot_start": "2026-01-19T09:00:00",
            "slot_end": "2026-01-19T10:00:00",
            "customer": {"firstName": "Test", "lastName": "User"},
            "vehicle": {"year": 2022, "make": "Toyota", "model": "Camry"},
        }
        response = test_client.post("/book", json=booking_request)
        assert response.status_code == 404

    def test_booking_invalid_email_returns_422(self, test_client):
        """Should return 422 for invalid email format."""
        booking_request = {
            "service_id": "svc-1",
            "slot_start": "2026-01-19T09:00:00",
            "slot_end": "2026-01-19T10:00:00",
            "customer": {
                "firstName": "Test",
                "lastName": "User",
                "email": "not-an-email",
            },
            "vehicle": {"year": 2022, "make": "Toyota", "model": "Camry"},
        }
        response = test_client.post("/book", json=booking_request)
        assert response.status_code == 422

    def test_booking_invalid_year_returns_422(self, test_client):
        """Should return 422 for invalid vehicle year."""
        booking_request = {
            "service_id": "svc-1",
            "slot_start": "2026-01-19T09:00:00",
            "slot_end": "2026-01-19T10:00:00",
            "customer": {"firstName": "Test", "lastName": "User"},
            "vehicle": {"year": 1800, "make": "Toyota", "model": "Camry"},
        }
        response = test_client.post("/book", json=booking_request)
        assert response.status_code == 422

    def test_booking_missing_required_fields_returns_422(self, test_client):
        """Should return 422 when required fields are missing."""
        booking_request = {
            "service_id": "svc-1",
            "slot_start": "2026-01-19T09:00:00",
            # Missing slot_end, customer, vehicle
        }
        response = test_client.post("/book", json=booking_request)
        assert response.status_code == 422

    def test_slot_conflict_returns_409(self, test_client, mock_shopmonkey_client):
        """Should return 409 when slot is no longer available.

        Conflict detection counts appointments with `orderId` set against
        the qualified tech count. With 2 qualified techs, we need 2
        overlapping orders to fill the slot.
        """
        mock_shopmonkey_client.get_appointments_for_date = AsyncMock(
            return_value=[
                {
                    "orderId": "ord-aaa",
                    "startDate": "2026-01-19T09:00:00-06:00",
                    "endDate": "2026-01-19T10:00:00-06:00",
                },
                {
                    "orderId": "ord-bbb",
                    "startDate": "2026-01-19T09:00:00-06:00",
                    "endDate": "2026-01-19T10:00:00-06:00",
                },
            ]
        )
        booking_request = {
            "service_id": "svc-1",
            "slot_start": "2026-01-19T09:00:00",
            "slot_end": "2026-01-19T10:00:00",
            "customer": {"firstName": "Test", "lastName": "User"},
            "vehicle": {"year": 2022, "make": "Toyota", "model": "Camry"},
        }
        response = test_client.post("/book", json=booking_request)
        assert response.status_code == 409
        assert "no longer available" in response.json()["detail"]

    def test_time_off_block_returns_409(self, test_client, mock_shopmonkey_client):
        """Both qualified techs are out on ticketless calendar blocks, so the
        booking must be refused. Before the time-off fix these entries were
        skipped entirely and the booking went through."""
        blocks = [
            {
                "id": "appt-ooo-1",
                "orderId": None,
                "startDate": "2026-01-19T09:00:00-06:00",
                "endDate": "2026-01-19T10:00:00-06:00",
                "name": "John Out",
            },
            {
                "id": "appt-ooo-2",
                "orderId": None,
                "startDate": "2026-01-19T09:00:00-06:00",
                "endDate": "2026-01-19T10:00:00-06:00",
                "name": "Jane Out",
            },
        ]
        mock_shopmonkey_client.get_appointments_for_date = AsyncMock(return_value=blocks)
        mock_shopmonkey_client.get_busy_techs_for_appointments = AsyncMock(
            return_value={"appt-ooo-1": {"tech-1"}, "appt-ooo-2": {"tech-2"}}
        )
        booking_request = {
            "service_id": "svc-1",
            "slot_start": "2026-01-19T09:00:00",
            "slot_end": "2026-01-19T10:00:00",
            "customer": {"firstName": "Test", "lastName": "User"},
            "vehicle": {"year": 2022, "make": "Toyota", "model": "Camry"},
        }
        response = test_client.post("/book", json=booking_request)
        assert response.status_code == 409
        assert "no longer available" in response.json()["detail"]
        mock_shopmonkey_client.create_appointment.assert_not_called()

    def test_customer_name_too_long_returns_422(self, test_client):
        """Should return 422 when customer name exceeds max length."""
        booking_request = {
            "service_id": "svc-1",
            "slot_start": "2026-01-19T09:00:00",
            "slot_end": "2026-01-19T10:00:00",
            "customer": {
                "firstName": "A" * 101,  # Exceeds 100 char limit
                "lastName": "User",
            },
            "vehicle": {"year": 2022, "make": "Toyota", "model": "Camry"},
        }
        response = test_client.post("/book", json=booking_request)
        assert response.status_code == 422


class TestAPIKeyAuthentication:
    """Tests for API key authentication."""

    def test_services_without_api_key_when_not_configured(self, test_client):
        """Should allow access when API_KEY is not configured."""
        response = test_client.get("/services")
        assert response.status_code == 200

    def test_services_without_api_key_when_configured(self, test_client_with_api_key):
        """Should return 401 when API_KEY is configured but not provided."""
        response = test_client_with_api_key.get("/services")
        assert response.status_code == 401
        assert "API key required" in response.json()["detail"]

    def test_services_with_invalid_api_key(self, test_client_with_api_key):
        """Should return 401 with invalid API key."""
        response = test_client_with_api_key.get("/services", headers={"X-API-Key": "wrong-key"})
        assert response.status_code == 401
        assert "Invalid API key" in response.json()["detail"]

    def test_services_with_valid_api_key(self, test_client_with_api_key):
        """Should allow access with valid API key."""
        response = test_client_with_api_key.get(
            "/services", headers={"X-API-Key": "test-api-key-123"}
        )
        assert response.status_code == 200

    def test_health_endpoint_no_auth_required(self, test_client_with_api_key):
        """Health endpoints should not require authentication."""
        response = test_client_with_api_key.get("/health")
        assert response.status_code == 200

    def test_schedule_endpoint_no_auth_required(self, test_client_with_api_key):
        """Schedule endpoint should not require authentication."""
        # Will return 404 since widget.html doesn't exist in test, but not 401
        response = test_client_with_api_key.get("/schedule")
        assert response.status_code in [200, 404]


class TestOpenAPISchema:
    """Tests for OpenAPI schema generation."""

    def test_openapi_schema_accessible(self, test_client):
        """Should be able to access OpenAPI schema."""
        response = test_client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        assert schema["info"]["title"] == "Shopmonkey Scheduling API"
        assert schema["info"]["version"] == "1.0.0"

    def test_docs_accessible(self, test_client):
        """Should be able to access Swagger UI docs."""
        response = test_client.get("/docs")
        assert response.status_code == 200


class TestInputValidation:
    """Tests for input validation constraints."""

    def test_empty_first_name_returns_422(self, test_client):
        """Should return 422 for empty first name."""
        booking_request = {
            "service_id": "svc-1",
            "slot_start": "2026-01-19T09:00:00",
            "slot_end": "2026-01-19T10:00:00",
            "customer": {
                "firstName": "",
                "lastName": "User",
            },
            "vehicle": {"year": 2022, "make": "Toyota", "model": "Camry"},
        }
        response = test_client.post("/book", json=booking_request)
        assert response.status_code == 422

    def test_invalid_phone_format_returns_422(self, test_client):
        """Should return 422 for invalid phone number."""
        booking_request = {
            "service_id": "svc-1",
            "slot_start": "2026-01-19T09:00:00",
            "slot_end": "2026-01-19T10:00:00",
            "customer": {
                "firstName": "Test",
                "lastName": "User",
                "phone": "abc123",  # Invalid phone
            },
            "vehicle": {"year": 2022, "make": "Toyota", "model": "Camry"},
        }
        response = test_client.post("/book", json=booking_request)
        assert response.status_code == 422

    def test_valid_phone_formats_accepted(self, test_client):
        """Should accept various valid phone formats."""
        valid_phones = [
            "5551234567",
            "555-123-4567",
            "(555) 123-4567",
            "+1-555-123-4567",
            "+15551234567",
        ]
        for phone in valid_phones:
            booking_request = {
                "service_id": "svc-1",
                "slot_start": "2026-01-19T09:00:00",
                "slot_end": "2026-01-19T10:00:00",
                "customer": {
                    "firstName": "Test",
                    "lastName": "User",
                    "phone": phone,
                },
                "vehicle": {"year": 2022, "make": "Toyota", "model": "Camry"},
            }
            response = test_client.post("/book", json=booking_request)
            # Should not fail validation (may get 200 or other non-422 status)
            assert response.status_code != 422, f"Phone '{phone}' should be valid"

    def test_vin_too_long_returns_422(self, test_client):
        """Should return 422 for VIN exceeding 17 characters."""
        booking_request = {
            "service_id": "svc-1",
            "slot_start": "2026-01-19T09:00:00",
            "slot_end": "2026-01-19T10:00:00",
            "customer": {"firstName": "Test", "lastName": "User"},
            "vehicle": {
                "year": 2022,
                "make": "Toyota",
                "model": "Camry",
                "vin": "A" * 18,  # Exceeds 17 char limit
            },
        }
        response = test_client.post("/book", json=booking_request)
        assert response.status_code == 422


class TestMultidayBooking:
    """Tests for multi-day booking creation in /book.

    Regression for Anne's June 3 report: a multi-day slot was booked as a
    single 30-minute stub (start → close on day 1) and the continuation
    day was never reserved. /book now derives the true span server-side
    and creates one appointment per spanned business day.
    """

    def _multiday_service(self):
        """A 2-hour service: at 16:00 with a 17:00 close it spans 2 days."""
        return {
            "id": "svc-1",
            "name": "Window Tint",
            "totalCents": 15000,
            "labels": [{"name": "Window Tint"}],
            "labors": [{"hours": 2.0}],
        }

    def _booking_request(self):
        # 16:00 Monday with a 17:00 close: 60 min day 1 + 60 min Tuesday.
        # slot_end mirrors what the widget sends for a multi-day slot:
        # the day-1 close time, NOT start + duration.
        return {
            "service_id": "svc-1",
            "slot_start": "2026-01-19T16:00:00",
            "slot_end": "2026-01-19T17:00:00",
            "customer": {"firstName": "Javion", "lastName": "Cotton"},
            "vehicle": {"year": 2026, "make": "Honda", "model": "Accord"},
        }

    def test_multiday_booking_creates_one_appointment_per_day(
        self, test_client, mock_shopmonkey_client
    ):
        mock_shopmonkey_client.get_canned_service = AsyncMock(return_value=self._multiday_service())
        mock_shopmonkey_client.create_appointment = AsyncMock(
            side_effect=[{"id": "appt-day1"}, {"id": "appt-day2"}]
        )

        response = test_client.post("/book", json=self._booking_request())
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        # The first day's appointment is the one returned to the customer.
        assert data["appointment_id"] == "appt-day1"
        confirmation = data["confirmation_number"]

        assert mock_shopmonkey_client.create_appointment.call_count == 2
        day1 = mock_shopmonkey_client.create_appointment.call_args_list[0].kwargs
        day2 = mock_shopmonkey_client.create_appointment.call_args_list[1].kwargs

        # Day 1: requested start through close (January -> CST, -06:00).
        assert day1["start_date"] == "2026-01-19T16:00:00.000-06:00"
        assert day1["end_date"] == "2026-01-19T17:00:00.000-06:00"
        # Day 2: open until the remaining 60 minutes are used up.
        assert day2["start_date"] == "2026-01-20T09:00:00.000-06:00"
        assert day2["end_date"] == "2026-01-20T10:00:00.000-06:00"

        # Both tiles labeled so staff see the linkage on the calendar.
        assert day1["title"].endswith("(Day 1 of 2)")
        assert day2["title"].endswith("(Day 2 of 2)")

        # Same confirmation, tech, and order linkage on every segment.
        assert confirmation in day1["notes"]
        assert confirmation in day2["notes"]
        assert "Multi-day service: 2 days" in day1["notes"]
        assert "Day 2 of 2" in day2["notes"]
        assert day1["technician_id"] == day2["technician_id"]
        assert day1["order_id"] == day2["order_id"]

    def test_multiday_booking_blocked_when_day2_full_returns_409(
        self, test_client, mock_shopmonkey_client
    ):
        """Day-2 conflicts must block the booking - the old code never
        looked at day 2 at all."""
        mock_shopmonkey_client.get_canned_service = AsyncMock(return_value=self._multiday_service())

        async def appointments_for(date_str, tech_ids=None):
            if date_str == "2026-01-20":
                # Two unattributed orders covering the morning fill both techs.
                return [
                    {
                        "orderId": "ord-a",
                        "startDate": "2026-01-20T09:00:00-06:00",
                        "endDate": "2026-01-20T11:00:00-06:00",
                    },
                    {
                        "orderId": "ord-b",
                        "startDate": "2026-01-20T09:00:00-06:00",
                        "endDate": "2026-01-20T11:00:00-06:00",
                    },
                ]
            return []

        mock_shopmonkey_client.get_appointments_for_date = AsyncMock(side_effect=appointments_for)

        response = test_client.post("/book", json=self._booking_request())
        assert response.status_code == 409
        assert "no longer available" in response.json()["detail"]
        mock_shopmonkey_client.create_appointment.assert_not_called()

    def test_multiday_booking_rolls_back_on_partial_failure(
        self, test_client, mock_shopmonkey_client
    ):
        """If day 2 creation fails, day 1 must be deleted - a half-created
        multi-day booking silently under-reserves the calendar."""
        from shopmonkey_client import ShopmonkeyAPIError

        mock_shopmonkey_client.get_canned_service = AsyncMock(return_value=self._multiday_service())
        mock_shopmonkey_client.create_appointment = AsyncMock(
            side_effect=[
                {"id": "appt-day1"},
                ShopmonkeyAPIError("boom", status_code=500),
            ]
        )
        mock_shopmonkey_client.delete_appointment = AsyncMock(return_value=True)

        response = test_client.post("/book", json=self._booking_request())
        assert response.status_code == 502

        mock_shopmonkey_client.delete_appointment.assert_called_once_with("appt-day1")

    def test_single_day_booking_derives_end_from_service_duration(
        self, test_client, mock_shopmonkey_client
    ):
        """The server-derived span wins over a bogus client slot_end."""
        # svc-1 fixture has estimatedDuration 60; client claims a 2h slot.
        booking_request = {
            "service_id": "svc-1",
            "slot_start": "2026-01-19T09:00:00",
            "slot_end": "2026-01-19T11:00:00",
            "customer": {"firstName": "Test", "lastName": "Customer"},
            "vehicle": {"year": 2022, "make": "Toyota", "model": "Camry"},
        }
        response = test_client.post("/book", json=booking_request)
        assert response.status_code == 200

        assert mock_shopmonkey_client.create_appointment.call_count == 1
        kwargs = mock_shopmonkey_client.create_appointment.call_args.kwargs
        assert kwargs["start_date"] == "2026-01-19T09:00:00.000-06:00"
        assert kwargs["end_date"] == "2026-01-19T10:00:00.000-06:00"
        # Single-day bookings keep the plain OOTB title - no day suffix.
        assert "(Day" not in kwargs["title"]


class TestSelectTechByPriority:
    """Tests for select_tech_by_priority - it decides who actually gets booked.

    Pure logic, but it is the only thing standing between "the senior tech
    gets every single job" and a fair rotation, and a wrong pick here shows
    up as a real person double-booked on the shop calendar.
    """

    TECHS = [
        {"tech_id": "t1", "tech_name": "Ann", "priority": 1},
        {"tech_id": "t2", "tech_name": "Bob", "priority": 1},
        {"tech_id": "t3", "tech_name": "Cid", "priority": 2},
    ]

    def test_returns_none_when_no_qualified_tech_is_free(self, main_module, clean_round_robin):
        """A free tech from another department must never be selected."""
        assert main_module.select_tech_by_priority(self.TECHS, ["someone-else"], "Tint") is None
        assert main_module.select_tech_by_priority(self.TECHS, [], "Tint") is None
        # No rotation state is created for a selection that never happened.
        assert "Tint" not in clean_round_robin

    def test_priority_beats_the_order_of_the_available_list(self, main_module, clean_round_robin):
        """Availability order is incidental; priority decides."""
        # t3 (priority 2) is listed first but t2 (priority 1) must win.
        assert main_module.select_tech_by_priority(self.TECHS, ["t3", "t2"], "Tint") == "t2"

    def test_sole_tech_at_top_priority_skips_round_robin(self, main_module, clean_round_robin):
        """With one tech at the top priority there is nothing to rotate, and
        no rotation state should be recorded for it."""
        assert main_module.select_tech_by_priority(self.TECHS, ["t1", "t3"], "Tint") == "t1"
        assert "Tint" not in clean_round_robin

    def test_rotates_between_techs_of_equal_priority(self, main_module, clean_round_robin):
        """Equal-priority techs alternate instead of the first one taking
        every booking."""
        picks = [
            main_module.select_tech_by_priority(self.TECHS, ["t1", "t2", "t3"], "Tint")
            for _ in range(4)
        ]
        assert picks == ["t1", "t2", "t1", "t2"]

    def test_rotation_is_tracked_per_department(self, main_module, clean_round_robin):
        """A booking in Vinyl must not advance the Window Tint rotation -
        otherwise a busy department starves techs in a quiet one."""
        assert main_module.select_tech_by_priority(self.TECHS, ["t1", "t2"], "Tint") == "t1"
        assert main_module.select_tech_by_priority(self.TECHS, ["t1", "t2"], "Vinyl") == "t1"
        assert main_module.select_tech_by_priority(self.TECHS, ["t1", "t2"], "Tint") == "t2"

    def test_rotation_is_tracked_per_priority_level(self, main_module, clean_round_robin):
        """Each priority tier keeps its own cursor, so falling back to the
        second tier doesn't scramble the first tier's rotation."""
        techs = [
            {"tech_id": "a1", "tech_name": "A1", "priority": 1},
            {"tech_id": "a2", "tech_name": "A2", "priority": 1},
            {"tech_id": "b1", "tech_name": "B1", "priority": 2},
            {"tech_id": "b2", "tech_name": "B2", "priority": 2},
        ]
        assert main_module.select_tech_by_priority(techs, ["a1", "a2"], "Tint") == "a1"
        # Only the second tier is free this time - it starts its own rotation.
        assert main_module.select_tech_by_priority(techs, ["b1", "b2"], "Tint") == "b1"
        # Back to the first tier: it resumes where it left off, at a2.
        assert main_module.select_tech_by_priority(techs, ["a1", "a2"], "Tint") == "a2"


class TestCannedServiceLineItemBuilders:
    """Tests for the canned_service_*_for_attach payload builders.

    These shape the real Shopmonkey ticket. A dropped or mis-scaled field
    means the customer is quoted one price on the widget and the shop's
    ticket shows another - the 2026-05-20 $130/hr-vs-$100/hr incident.
    """

    def test_labor_forwards_rate_references_only_when_present(self, main_module):
        """rateId must survive (without it Shopmonkey substitutes the shop
        default rate), but null/blank references must be omitted rather than
        sent as unset pointers."""
        labors = main_module.canned_service_labors_for_attach(
            {
                "name": "Window Tint",
                "labors": [
                    {
                        "hours": 1.0,
                        "rateCents": 10000,
                        "rateId": "lr_1",
                        "laborMatrixId": None,
                        "categoryId": "",
                    }
                ],
            }
        )
        assert labors[0]["rateId"] == "lr_1"
        assert "laborMatrixId" not in labors[0]
        assert "categoryId" not in labors[0]
        # A labor line with no name of its own inherits the service name so
        # the ticket line isn't blank.
        assert labors[0]["name"] == "Window Tint"

    def test_labor_cents_are_rounded_not_truncated(self, main_module):
        """Shopmonkey returns floats in cents fields; truncating loses a cent
        per line and the ticket total stops matching the quote."""
        labors = main_module.canned_service_labors_for_attach(
            {
                "labors": [
                    {
                        "name": "L",
                        "hours": 2,
                        "rateCents": 5649.5,
                        "discountCents": 1010.6,
                        "discountPercent": 12.5,
                    }
                ]
            }
        )
        assert labors[0]["rateCents"] == 5650
        assert labors[0]["discountCents"] == 1011
        # Percent is not a cents field - forwarded as-is.
        assert labors[0]["discountPercent"] == 12.5

    def test_labor_taxable_false_is_forwarded_and_absent_stays_absent(self, main_module):
        """taxable=False is meaningful data; dropping it would silently tax a
        non-taxable labor line."""
        taxed = main_module.canned_service_labors_for_attach(
            {"labors": [{"name": "L", "hours": 1, "rateCents": 1, "taxable": False}]}
        )
        assert taxed[0]["taxable"] is False
        bare = main_module.canned_service_labors_for_attach(
            {"labors": [{"name": "L", "hours": 1, "rateCents": 1}]}
        )
        assert "taxable" not in bare[0]

    def test_labor_missing_and_malformed_values_degrade_to_zero(self, main_module):
        """Garbage in a cents field must not take down the whole booking."""
        labors = main_module.canned_service_labors_for_attach(
            {"labors": [{"name": "L"}, {"name": "M", "hours": None, "rateCents": "n/a"}]}
        )
        assert labors[0]["hours"] == 0
        assert labors[0]["rateCents"] == 0
        assert labors[1]["hours"] == 0
        assert labors[1]["rateCents"] == 0

    def test_absent_or_null_collections_yield_empty_lists(self, main_module):
        """Shopmonkey omits these keys entirely on simple services."""
        assert main_module.canned_service_labors_for_attach({"labors": None}) == []
        assert main_module.canned_service_parts_for_attach({}) == []
        assert main_module.canned_service_fees_for_attach({"fees": None}) == []
        assert main_module.canned_service_subcontracts_for_attach({}) == []

    def test_part_forwards_inventory_references_note_and_discounts(self, main_module):
        parts = main_module.canned_service_parts_for_attach(
            {
                "parts": [
                    {
                        "name": "Ceramic IR",
                        "quantity": 3.5,
                        "retailCostCents": 1543.6,
                        "wholesaleCostCents": 0,
                        "partNumber": "CIR",
                        "taxable": True,
                        "discountCents": 100.6,
                        "discountPercent": 10,
                        "vendorId": "v1",
                        "inventoryPartId": "ip1",
                        "categoryId": None,
                        "pricingMatrixId": "pm1",
                        "note": "3.5 yards",
                    }
                ]
            }
        )
        part = parts[0]
        assert part["quantity"] == 3.5
        assert part["retailCostCents"] == 1544
        # A zero wholesale cost is real data ("we got it free"), not missing -
        # a truthiness check here would drop it and inflate reported margin.
        assert part["wholesaleCostCents"] == 0
        assert part["partNumber"] == "CIR"
        assert part["taxable"] is True
        assert part["discountCents"] == 101
        assert part["discountPercent"] == 10
        assert part["vendorId"] == "v1"
        assert part["inventoryPartId"] == "ip1"
        assert part["pricingMatrixId"] == "pm1"
        assert "categoryId" not in part
        assert part["note"] == "3.5 yards"

    def test_part_with_only_a_name_sends_nothing_extra(self, main_module):
        """Optional keys must be omitted, not sent as nulls."""
        parts = main_module.canned_service_parts_for_attach({"parts": [{"name": "Film"}]})
        assert parts[0] == {"name": "Film", "quantity": 0, "retailCostCents": 0}

    def test_fee_payload_shape(self, main_module):
        fees = main_module.canned_service_fees_for_attach(
            {
                "fees": [
                    {
                        "name": "Shop Supplies",
                        "amountCents": 1250.6,
                        "taxable": False,
                        "categoryId": "cat1",
                        "inventoryFeeId": "if1",
                    },
                    {},
                ]
            }
        )
        assert fees[0] == {
            "name": "Shop Supplies",
            "amountCents": 1251,
            "taxable": False,
            "categoryId": "cat1",
            "inventoryFeeId": "if1",
        }
        # A completely empty fee still produces a well-formed line.
        assert fees[1] == {"name": "", "amountCents": 0}

    def test_subcontract_reads_both_legacy_and_current_cost_fields(self, main_module):
        """The REST API moved wholesaleCostCents -> costCents; during the
        transition either one may be the only field present, and dropping the
        cost turns a subcontract into pure profit on the ticket."""
        subs = main_module.canned_service_subcontracts_for_attach(
            {
                "subcontracts": [
                    {
                        "name": "Dent removal",
                        "retailCostCents": 20000.4,
                        "wholesaleCostCents": 12000,
                        "costCents": 999,
                        "taxable": True,
                        "vendorId": "v9",
                    },
                    {"name": "Glass", "retailCostCents": 5000, "costCents": 3000.6},
                    {"name": "Bare"},
                ]
            }
        )
        # Legacy field wins when both are present.
        assert subs[0]["wholesaleCostCents"] == 12000
        assert subs[0]["retailCostCents"] == 20000
        assert subs[0]["taxable"] is True
        assert subs[0]["vendorId"] == "v9"
        # Falls back to the current field.
        assert subs[1]["wholesaleCostCents"] == 3001
        # Neither present: no cost key at all rather than a bogus zero.
        assert subs[2] == {"name": "Bare", "retailCostCents": 0}

    def test_attach_payload_stamps_assigned_tech_on_every_labor(self, main_module):
        """Every labor must carry technicianId, otherwise the next
        /availability check walks order.services.labors.technicianId, finds
        nothing, and treats this booking as unattributed - over-allowing the
        tech we just booked."""
        payload = main_module.build_attach_service_payload(
            {
                "name": "Tint",
                "labors": [
                    {"name": "A", "hours": 1, "rateCents": 1},
                    {"name": "B", "hours": 2, "rateCents": 2},
                ],
            },
            "svc-1",
            "tech-7",
        )
        assert [lab["technicianId"] for lab in payload["labors"]] == ["tech-7", "tech-7"]
        assert payload["cannedServiceId"] == "svc-1"
        assert payload["name"] == "Tint"
        # Every collection key is always present so the API sees an explicit
        # empty list rather than a missing field.
        assert payload["parts"] == []
        assert payload["fees"] == []
        assert payload["subcontracts"] == []

    def test_attach_payload_omits_tech_when_none_assigned(self, main_module):
        payload = main_module.build_attach_service_payload(
            {"name": "Tint", "labors": [{"name": "A", "hours": 1, "rateCents": 1}]},
            "svc-1",
            None,
        )
        assert "technicianId" not in payload["labors"][0]


MULTIDAY_SERVICE = {
    "id": "svc-1",
    "name": "Window Tint",
    "totalCents": 15000,
    "labels": [{"name": "Window Tint"}],
    "labors": [{"hours": 2.0}],
}

# 16:00 Monday against a 17:00 close: 60 min day 1 + 60 min Tuesday.
MULTIDAY_BOOKING = {
    "service_id": "svc-1",
    "slot_start": "2026-01-19T16:00:00",
    "slot_end": "2026-01-19T17:00:00",
    "customer": {"firstName": "Javion", "lastName": "Cotton"},
    "vehicle": {"year": 2026, "make": "Honda", "model": "Accord"},
}

SINGLE_DAY_BOOKING = {
    "service_id": "svc-1",
    "slot_start": "2026-01-19T09:00:00",
    "slot_end": "2026-01-19T10:00:00",
    "customer": {"firstName": "Test", "lastName": "Customer"},
    "vehicle": {"year": 2022, "make": "Toyota", "model": "Camry"},
}


def _config_with(mock_config, **extra):
    """A copy of the standard test config plus the given top-level keys."""
    return {**mock_config, **extra}


class TestBookingFailureModes:
    """Tests for the /book error and fallback branches.

    Every branch here is a way a booking can go wrong halfway through. The
    thing that must never happen is a customer getting a confirmation number
    for an appointment that isn't really on the calendar (or vice versa).
    """

    def test_returns_500_when_clients_are_not_initialized(
        self, test_client, mock_shopmonkey_client
    ):
        """A boot-time client failure must refuse bookings, not half-book.

        The status alone proves nothing - an uncaught AttributeError on the
        missing client lands on the same 500. The DISTINCT detail is what
        separates "we deliberately declined" from "we crashed", and
        create_appointment must never have been reached.
        """
        with patch("main.shopmonkey_client", None):
            response = test_client.post("/book", json=SINGLE_DAY_BOOKING)
        assert response.status_code == 500
        assert response.json()["detail"] == "Service temporarily unavailable"
        mock_shopmonkey_client.create_appointment.assert_not_called()

    def test_customer_without_id_aborts_before_any_calendar_write(
        self, test_client, mock_shopmonkey_client
    ):
        """If Shopmonkey hands back a customer with no id we cannot link an
        appointment to anyone - stop before creating a vehicle or a booking."""
        mock_shopmonkey_client.find_or_create_customer = AsyncMock(return_value={})
        response = test_client.post("/book", json=SINGLE_DAY_BOOKING)
        assert response.status_code == 500
        assert response.json()["detail"] == "Unable to process booking"
        mock_shopmonkey_client.find_or_create_vehicle.assert_not_called()
        mock_shopmonkey_client.create_appointment.assert_not_called()

    def test_vehicle_without_id_aborts_before_any_calendar_write(
        self, test_client, mock_shopmonkey_client
    ):
        """An appointment with no vehicle is useless to the shop."""
        mock_shopmonkey_client.find_or_create_vehicle = AsyncMock(return_value={})
        response = test_client.post("/book", json=SINGLE_DAY_BOOKING)
        assert response.status_code == 500
        assert response.json()["detail"] == "Unable to process booking"
        mock_shopmonkey_client.create_appointment.assert_not_called()

    def test_unexpected_error_returns_generic_500(self, test_client, mock_shopmonkey_client):
        """Internal failures return 500 without leaking the exception text to
        the public widget."""
        mock_shopmonkey_client.find_or_create_customer = AsyncMock(
            side_effect=ValueError("postgres password is hunter2")
        )
        response = test_client.post("/book", json=SINGLE_DAY_BOOKING)
        assert response.status_code == 500
        assert response.json()["detail"] == "An unexpected error occurred"

    def test_shopmonkey_error_during_booking_returns_502(self, test_client, mock_shopmonkey_client):
        """An upstream outage is a 502, distinct from our own 500s."""
        from shopmonkey_client import ShopmonkeyAPIError

        mock_shopmonkey_client.find_or_create_customer = AsyncMock(
            side_effect=ShopmonkeyAPIError("upstream down", status_code=503)
        )
        response = test_client.post("/book", json=SINGLE_DAY_BOOKING)
        assert response.status_code == 502
        assert response.json()["detail"] == "Unable to complete booking"

    def _order_config(self, mock_config):
        return _config_with(
            mock_config,
            online_booking={
                "create_order": True,
                "workflow_status_name": "Scheduled",
                "order_status": "Estimate",
                "order_color": "blue",
            },
        )

    def test_workflow_status_lookup_failure_still_books_the_appointment(
        self, client_factory, mock_config, mock_shopmonkey_client
    ):
        """The ticket is a nice-to-have; the calendar entry is not. A failed
        workflow-status lookup must degrade to appointment-only, not 500."""
        mock_shopmonkey_client.get_workflow_status_id = AsyncMock(side_effect=Exception("boom"))
        mock_shopmonkey_client.create_order = AsyncMock()
        with client_factory(self._order_config(mock_config)) as client:
            response = client.post("/book", json=SINGLE_DAY_BOOKING)
        assert response.status_code == 200
        mock_shopmonkey_client.create_order.assert_not_awaited()
        assert mock_shopmonkey_client.create_appointment.call_args.kwargs["order_id"] is None

    def test_unknown_workflow_status_skips_order_creation(
        self, client_factory, mock_config, mock_shopmonkey_client
    ):
        """A renamed workflow column in Shopmonkey must not break booking."""
        mock_shopmonkey_client.get_workflow_status_id = AsyncMock(return_value=None)
        mock_shopmonkey_client.create_order = AsyncMock()
        with client_factory(self._order_config(mock_config)) as client:
            response = client.post("/book", json=SINGLE_DAY_BOOKING)
        assert response.status_code == 200
        mock_shopmonkey_client.create_order.assert_not_awaited()
        assert mock_shopmonkey_client.create_appointment.call_args.kwargs["order_id"] is None

    def test_order_creation_failure_falls_back_to_appointment_only(
        self, client_factory, mock_config, mock_shopmonkey_client
    ):
        """Order creation failing must not lose the customer's slot."""
        from shopmonkey_client import ShopmonkeyAPIError

        mock_shopmonkey_client.get_workflow_status_id = AsyncMock(return_value="ws_1")
        mock_shopmonkey_client.create_order = AsyncMock(
            side_effect=ShopmonkeyAPIError("order rejected", status_code=422)
        )
        with client_factory(self._order_config(mock_config)) as client:
            response = client.post("/book", json=SINGLE_DAY_BOOKING)
        assert response.status_code == 200
        assert response.json()["success"] is True
        # The appointment is created unlinked rather than pointing at an order
        # that does not exist.
        assert mock_shopmonkey_client.create_appointment.call_args.kwargs["order_id"] is None

    def test_rollback_failure_does_not_mask_the_original_error(
        self, test_client, mock_shopmonkey_client
    ):
        """If deleting the orphaned day-1 appointment also fails we still
        report the booking as failed - never a success on a half-reserved
        calendar."""
        from shopmonkey_client import ShopmonkeyAPIError

        mock_shopmonkey_client.get_canned_service = AsyncMock(return_value=MULTIDAY_SERVICE)
        mock_shopmonkey_client.create_appointment = AsyncMock(
            side_effect=[{"id": "appt-day1"}, ShopmonkeyAPIError("day 2 failed", status_code=500)]
        )
        mock_shopmonkey_client.delete_appointment = AsyncMock(
            side_effect=RuntimeError("delete also failed")
        )
        response = test_client.post("/book", json=MULTIDAY_BOOKING)
        assert response.status_code == 502
        mock_shopmonkey_client.delete_appointment.assert_awaited_once_with("appt-day1")

    def test_assigns_the_only_free_tech_and_names_them_in_the_notes(
        self, test_client, mock_shopmonkey_client
    ):
        """When the priority-1 tech is booked solid, the priority-2 tech must
        get both the assignment and the note staff read off the ticket."""
        mock_shopmonkey_client.get_appointments_for_date = AsyncMock(
            return_value=[
                {
                    "id": "appt-busy",
                    "orderId": "ord-1",
                    "startDate": "2026-01-19T09:00:00-06:00",
                    "endDate": "2026-01-19T10:00:00-06:00",
                }
            ]
        )
        mock_shopmonkey_client.get_busy_techs_for_appointments = AsyncMock(
            return_value={"appt-busy": {"tech-1"}}
        )
        response = test_client.post("/book", json=SINGLE_DAY_BOOKING)
        assert response.status_code == 200
        kwargs = mock_shopmonkey_client.create_appointment.call_args.kwargs
        assert kwargs["technician_id"] == "tech-2"
        assert "Assign to: Jane Smith" in kwargs["notes"]

    def test_null_phone_is_accepted(self, test_client):
        """The widget sends phone: null when the field is left blank."""
        request = {
            **SINGLE_DAY_BOOKING,
            "customer": {"firstName": "A", "lastName": "B", "phone": None},
        }
        response = test_client.post("/book", json=request)
        assert response.status_code == 200


class TestBookingEmailNotification:
    """Tests for the fire-and-forget booking notification."""

    def _email_client(self):
        email = MagicMock()
        email.enabled = True
        email.send_booking_notification = AsyncMock(return_value=True)
        return email

    def test_notification_spans_the_full_multiday_booking(
        self, test_client, mock_shopmonkey_client
    ):
        """The email renders its date range and overnight note off
        start_time/end_time. Taking these from the client-submitted slot
        would tell the customer to pick their car up the same evening when
        it is actually staying overnight."""
        mock_shopmonkey_client.get_canned_service = AsyncMock(return_value=MULTIDAY_SERVICE)
        mock_shopmonkey_client.create_appointment = AsyncMock(
            side_effect=[{"id": "appt-day1"}, {"id": "appt-day2"}]
        )
        email = self._email_client()
        with patch("main.get_email_client", return_value=email):
            response = test_client.post("/book", json=MULTIDAY_BOOKING)

        assert response.status_code == 200
        email.send_booking_notification.assert_called_once()
        details = email.send_booking_notification.call_args.args[0]
        assert details.confirmation_number == response.json()["confirmation_number"]
        assert details.start_time == datetime(2026, 1, 19, 16, 0)
        # Last segment's end, on the FOLLOWING day.
        assert details.end_time == datetime(2026, 1, 20, 10, 0)
        assert details.technician_name == "John Doe"
        assert details.service_name == "Window Tint"
        assert details.customer_first_name == "Javion"
        assert details.vehicle_make == "Honda"

    def test_no_notification_when_email_is_disabled(self, test_client):
        """A shop with no SMTP configured must not have send attempted."""
        email = self._email_client()
        email.enabled = False
        with patch("main.get_email_client", return_value=email):
            response = test_client.post("/book", json=SINGLE_DAY_BOOKING)
        assert response.status_code == 200
        email.send_booking_notification.assert_not_called()


class TestServicesEndpointErrors:
    """Tests for /services failure paths and optional-field derivation."""

    def test_returns_500_when_client_not_initialized(self, test_client):
        """The guard's distinct detail is the only thing separating a
        deliberate refusal from an AttributeError on the missing client -
        both of which surface as a bare 500."""
        with patch("main.shopmonkey_client", None):
            response = test_client.get("/services")
        assert response.status_code == 500
        assert response.json()["detail"] == "Service temporarily unavailable"

    def test_upstream_outage_returns_502(self, test_client, mock_shopmonkey_client):
        """Distinguish "Shopmonkey is down" from "we are broken" so the widget
        can tell the customer to try again later."""
        from shopmonkey_client import ShopmonkeyAPIError

        mock_shopmonkey_client.get_bookable_canned_services = AsyncMock(
            side_effect=ShopmonkeyAPIError("gateway", status_code=502)
        )
        response = test_client.get("/services")
        assert response.status_code == 502

    def test_unexpected_error_returns_generic_500(self, test_client, mock_shopmonkey_client):
        mock_shopmonkey_client.get_bookable_canned_services = AsyncMock(
            side_effect=RuntimeError("api token leaked here")
        )
        response = test_client.get("/services")
        assert response.status_code == 500
        assert response.json()["detail"] == "An unexpected error occurred"

    def test_service_without_labels_or_labors_reports_nulls(
        self, test_client, mock_shopmonkey_client
    ):
        """Missing optional data must come back as null rather than crashing
        the list - one malformed service must not hide the whole catalog.

        Pinned alongside the POSITIVE branch: when a label IS present its
        name becomes `category`, which is what the widget builds its service
        tabs from. Without that assertion a hard-coded `category=None` looks
        identical to correct null handling.
        """
        labeled = test_client.get("/services")
        assert labeled.status_code == 200
        # svc-1 in the default fixture carries labels[0].name == "Window Tint".
        assert labeled.json()["services"][0]["category"] == "Window Tint"

        mock_shopmonkey_client.get_bookable_canned_services = AsyncMock(
            return_value=[
                {"id": "svc-bare", "name": "Bare", "priceCents": 999},
                {"id": "svc-zero", "name": "Zero", "labels": [{}], "labors": [{"hours": 0}]},
            ]
        )
        response = test_client.get("/services")
        assert response.status_code == 200
        bare, zero = response.json()["services"]
        assert bare["category"] is None
        assert bare["laborHours"] is None
        # priceCents is the fallback when totalCents is absent.
        assert bare["totalCents"] == 999
        # A label with no name, and labors summing to zero, are both "unknown".
        assert zero["category"] is None
        assert zero["laborHours"] is None


class TestDisabledDepartments:
    """Tests for the disabled_departments config gate.

    Turning a department off in config must remove it from the catalog AND
    refuse direct booking attempts - a service hidden from the widget that
    can still be booked by URL is worse than not hiding it at all.
    """

    def _config(self, mock_config, disabled):
        return _config_with(mock_config, disabled_departments=disabled)

    def test_disabled_department_is_filtered_from_the_catalog(self, client_factory, mock_config):
        with client_factory(self._config(mock_config, {"Vinyl": {"except": ["ppf"]}})) as client:
            response = client.get("/services")
        ids = [s["id"] for s in response.json()["services"]]
        # svc-2 ("Paint Protection Film", label Vinyl) is gone; svc-1 stays.
        assert ids == ["svc-1"]

    def test_exception_list_keeps_matching_services_bookable(
        self, client_factory, mock_config, mock_shopmonkey_client
    ):
        """The `except` list is a substring escape hatch on the service name."""
        mock_shopmonkey_client.get_bookable_canned_services = AsyncMock(
            return_value=[
                {"id": "svc-ppf", "name": "PPF Partial Front", "labels": [{"name": "Vinyl"}]},
                {"id": "svc-wrap", "name": "Full Vinyl Wrap", "labels": [{"name": "Vinyl"}]},
            ]
        )
        with client_factory(self._config(mock_config, {"Vinyl": {"except": ["ppf"]}})) as client:
            response = client.get("/services")
        assert [s["id"] for s in response.json()["services"]] == ["svc-ppf"]

    def test_department_disabled_with_no_exceptions_block(self, client_factory, mock_config):
        """`Vinyl:` with an empty body in YAML parses as None - it must still
        disable the whole department instead of raising."""
        with client_factory(self._config(mock_config, {"Vinyl": None})) as client:
            response = client.get("/services")
        assert [s["id"] for s in response.json()["services"]] == ["svc-1"]

    def test_disabled_service_cannot_be_booked_directly(
        self, client_factory, mock_config, mock_shopmonkey_client
    ):
        """Hidden from the catalog must also mean 404 on /availability and
        /book, and indistinguishable from a service that does not exist."""
        with client_factory(
            self._config(mock_config, {"Window Tint": {"except": ["ceramic"]}})
        ) as client:
            availability = client.get("/availability?service_id=svc-1&date=2026-01-19")
            booking = client.post("/book", json=SINGLE_DAY_BOOKING)
        assert availability.status_code == 404
        assert booking.status_code == 404
        assert booking.json()["detail"] == "Service not found"
        mock_shopmonkey_client.create_appointment.assert_not_called()

    def test_service_matching_the_exception_stays_bookable(
        self, client_factory, mock_config, mock_shopmonkey_client
    ):
        mock_shopmonkey_client.get_canned_service = AsyncMock(
            return_value={
                "id": "svc-1",
                "name": "Window Tint - Ceramic",
                "labels": [{"name": "Window Tint"}],
                "estimatedDuration": 60,
            }
        )
        with client_factory(
            self._config(mock_config, {"Window Tint": {"except": ["ceramic"]}})
        ) as client:
            response = client.get("/availability?service_id=svc-1&date=2026-01-19")
        assert response.status_code == 200


class TestAvailabilityFailureModes:
    """Tests for /availability failure paths shared with the booking flow."""

    def test_returns_500_when_clients_are_not_initialized(self, test_client):
        with patch("main.sheets_client", None):
            response = test_client.get("/availability?service_id=svc-1&date=2026-01-19")
        assert response.status_code == 500

    def test_shopmonkey_outage_returns_502_not_404(self, test_client, mock_shopmonkey_client):
        """An outage must not be reported as "service not found" - that would
        make the widget permanently hide a service that still exists."""
        from shopmonkey_client import ShopmonkeyAPIError

        mock_shopmonkey_client.get_canned_service = AsyncMock(
            side_effect=ShopmonkeyAPIError("gateway", status_code=502)
        )
        response = test_client.get("/availability?service_id=svc-1&date=2026-01-19")
        assert response.status_code == 502

    def test_sheets_outage_returns_502_not_404(self, test_client, mock_sheets_client):
        """Same for the tech matrix: an unreachable sheet is not "no techs"."""
        mock_sheets_client.get_techs_for_department = AsyncMock(
            side_effect=Exception("sheets quota exceeded")
        )
        response = test_client.get("/availability?service_id=svc-1&date=2026-01-19")
        assert response.status_code == 502

    def test_label_with_blank_name_returns_404(self, test_client, mock_shopmonkey_client):
        """A label present but unnamed maps to no department."""
        mock_shopmonkey_client.get_canned_service = AsyncMock(
            return_value={"id": "svc-x", "name": "Mystery", "labels": [{"name": ""}]}
        )
        response = test_client.get("/availability?service_id=svc-x&date=2026-01-19")
        assert response.status_code == 404

    def test_future_day_fetch_failure_does_not_break_the_day(
        self, test_client, mock_shopmonkey_client
    ):
        """A multi-day service needs tomorrow's calendar to judge late slots.
        If that fetch fails we still serve today's slots rather than 500."""
        from shopmonkey_client import ShopmonkeyAPIError

        mock_shopmonkey_client.get_canned_service = AsyncMock(return_value=MULTIDAY_SERVICE)

        async def appointments_for(date_str, tech_ids=None):
            if date_str != "2026-01-19":
                raise ShopmonkeyAPIError("tomorrow unavailable", status_code=500)
            return []

        mock_shopmonkey_client.get_appointments_for_date = AsyncMock(side_effect=appointments_for)
        response = test_client.get("/availability?service_id=svc-1&date=2026-01-19")
        assert response.status_code == 200
        assert response.json()["slots"], "today's slots should still be offered"

    def test_unexpected_error_returns_generic_500(self, test_client):
        with patch("main.calculate_available_slots", side_effect=RuntimeError("internal detail")):
            response = test_client.get("/availability?service_id=svc-1&date=2026-01-19")
        assert response.status_code == 500
        assert response.json()["detail"] == "An unexpected error occurred"

    def test_duration_includes_the_configured_buffer(
        self, client_factory, mock_config, mock_shopmonkey_client
    ):
        """Cure/buffer time is part of the advertised slot length, otherwise
        the next customer is booked on top of a curing vehicle."""
        mock_shopmonkey_client.get_canned_service = AsyncMock(
            return_value={
                "id": "svc-1",
                "name": "Window Tint",
                "labels": [{"name": "Window Tint"}],
                "labors": [{"hours": 1.0}],
            }
        )
        config = _config_with(mock_config, service_buffers={"Window Tint": 30})
        with client_factory(config) as client:
            response = client.get("/availability?service_id=svc-1&date=2026-01-19")
        # 60 min of labor + 30 min of cure time.
        assert response.json()["duration_minutes"] == 90


class TestReadinessProbe:
    """Tests for /health/ready - it gates traffic in Cloud Run."""

    def test_healthy_dependencies_return_200_with_cache_status(
        self, test_client, mock_sheets_client
    ):
        """Field-by-field rather than whole-payload equality: adding a new
        field to ReadinessResponse is not a regression, but a dropped or
        wrong status - or a cache block that stops reflecting the sheets
        client - is."""
        response = test_client.get("/health/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["shopmonkey"] == "healthy"
        assert data["sheets"] == "healthy"
        # The probe must surface the live cache block, not a static stub.
        assert data["sheets_cache"] == mock_sheets_client.get_cache_status.return_value

    def test_shopmonkey_reporting_unhealthy_degrades_to_503(
        self, test_client, mock_shopmonkey_client
    ):
        mock_shopmonkey_client.health_check = AsyncMock(return_value=False)
        response = test_client.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["shopmonkey"] == "unhealthy"

    def test_shopmonkey_raising_is_treated_as_unhealthy(self, test_client, mock_shopmonkey_client):
        """A raising health check must not 500 the probe itself."""
        mock_shopmonkey_client.health_check = AsyncMock(side_effect=RuntimeError("connect fail"))
        response = test_client.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["shopmonkey"] == "unhealthy"

    def test_sheets_raising_is_treated_as_unhealthy(self, test_client, mock_sheets_client):
        mock_sheets_client.health_check = AsyncMock(side_effect=RuntimeError("no creds"))
        response = test_client.get("/health/ready")
        assert response.status_code == 503
        data = response.json()
        assert data["sheets"] == "unhealthy"
        assert data["status"] == "degraded"


class TestCorsOrigins:
    """Tests for get_cors_origins - it decides who may call the API."""

    def test_unset_means_no_origins(self, main_module):
        with patch.dict(os.environ, {"ALLOWED_ORIGINS": ""}, clear=False):
            assert main_module.get_cors_origins() == []

    def test_wildcard_is_passed_through_intact(self, main_module):
        """ "*" must stay a wildcard, not become a literal origin named "*"."""
        with patch.dict(os.environ, {"ALLOWED_ORIGINS": "*"}, clear=False):
            assert main_module.get_cors_origins() == ["*"]

    def test_comma_list_is_split_and_trimmed(self, main_module):
        with patch.dict(
            os.environ,
            {"ALLOWED_ORIGINS": " https://a.example , https://b.example ,, "},
            clear=False,
        ):
            assert main_module.get_cors_origins() == ["https://a.example", "https://b.example"]


class TestStartupFailures:
    """Tests for lifespan - a misconfigured deploy must refuse to serve.

    Booting "successfully" with no config or no credentials would let Cloud
    Run route live traffic at an app that 500s every request.
    """

    def _boot(self, match, **patches):
        """Boot the app expecting a RuntimeError whose message matches.

        `match` is required: a bare pytest.raises(RuntimeError) is satisfied
        by ANY startup RuntimeError, so all three cases below would pass on
        a single unrelated failure and the distinct abort reasons - which
        are what an operator reads out of the Cloud Run logs - would go
        unverified.
        """
        from main import app

        with patch.dict(os.environ, {"API_KEY": "", "ALLOWED_ORIGINS": ""}, clear=False):
            with patch.multiple("main", **patches):
                with pytest.raises(RuntimeError, match=match):
                    with TestClient(app):
                        pass

    def test_missing_config_file_aborts_startup(self, mock_shopmonkey_client, mock_sheets_client):
        self._boot(
            match=r"^Configuration file not found: config\.yaml$",
            load_config=MagicMock(side_effect=FileNotFoundError("config.yaml")),
            ShopmonkeyClient=MagicMock(return_value=mock_shopmonkey_client),
            SheetsClient=MagicMock(return_value=mock_sheets_client),
        )

    def test_invalid_config_aborts_startup(self, mock_shopmonkey_client, mock_sheets_client):
        self._boot(
            match=r"^Invalid configuration: business_hours missing$",
            load_config=MagicMock(return_value={}),
            validate_config=MagicMock(side_effect=ValueError("business_hours missing")),
            ShopmonkeyClient=MagicMock(return_value=mock_shopmonkey_client),
            SheetsClient=MagicMock(return_value=mock_sheets_client),
        )

    def test_missing_credentials_abort_startup(self, mock_config):
        self._boot(
            match=r"^Failed to initialize clients: SHOPMONKEY_API_TOKEN not set$",
            load_config=MagicMock(return_value=mock_config),
            validate_config=MagicMock(),
            ShopmonkeyClient=MagicMock(side_effect=ValueError("SHOPMONKEY_API_TOKEN not set")),
        )


class TestModuleHelpers:
    """Tests for small helpers the endpoints lean on."""

    def test_now_local_reads_the_clock_of_the_given_zone_and_is_naive(self, main_module):
        """Two contracts, both load-bearing for the elapsed-slot guards.

        Naiveness: the comparisons in /availability and /book are against
        naive datetimes, so a tz-aware return would raise on every request.

        The clock itself: _now_local must report wall time IN THE PASSED
        ZONE. Reading UTC instead would move "now" ~6 hours forward, which
        silently deletes the whole morning from /availability and lets the
        /book past-slot 409 fire on slots that have not happened yet.
        """
        from availability import get_timezone

        now = main_module._now_local(get_timezone({}))
        assert now.tzinfo is None

        central = ZoneInfo("America/Chicago")
        central_now = main_module._now_local(central)
        utc_now = main_module._now_local(ZoneInfo("UTC"))
        assert central_now.tzinfo is None
        assert utc_now.tzinfo is None

        # Central trails UTC by its current offset: 6h in CST, 5h in CDT.
        expected = -datetime.now(central).utcoffset()
        assert expected in (timedelta(hours=5), timedelta(hours=6))
        assert abs((utc_now - central_now) - expected) < timedelta(seconds=5)

    async def test_fetch_appointments_tags_busy_tech_ids(self, main_module):
        """Each appointment is annotated with the union of directly assigned
        technicians and the ones reached through its order's labors - that
        annotation is what lets availability drop only the busy techs."""
        client = AsyncMock()
        client.get_appointments_for_date = AsyncMock(
            return_value=[{"id": "a1"}, {"id": "a2"}, {"id": "a3"}]
        )
        client.get_busy_techs_for_appointments = AsyncMock(
            return_value={"a1": {"tech-2", "tech-1"}, "a2": set()}
        )
        with patch("main.shopmonkey_client", client):
            appointments = await main_module._fetch_appointments_with_busy_techs("2026-01-19")

        by_id = {a["id"]: a["_busyTechIds"] for a in appointments}
        assert by_id["a1"] == ["tech-1", "tech-2"]
        assert by_id["a2"] == []
        # An appointment absent from the map is unattributed, not busy.
        assert by_id["a3"] == []

    async def test_fetch_appointments_without_a_client_returns_empty(self, main_module):
        with patch("main.shopmonkey_client", None):
            assert await main_module._fetch_appointments_with_busy_techs("2026-01-19") == []

    def test_schedule_page_404s_when_the_widget_is_missing(self, test_client):
        """A broken image build must surface as 404, not a stack trace."""
        with patch("main.static_dir", "/nonexistent-static-dir"):
            response = test_client.get("/schedule")
        assert response.status_code == 404


class TestBookingNeverConfirmsWithoutATechnician:
    """Regression guard for the multi-day no-technician booking.

    Capacity now derives from the same cross-day intersection it returns, so an
    available slot always has at least one tech and this guard should be
    unreachable. It is kept because confirming unassigned is bad enough to
    warrant defense in depth: the customer holds a confirmation for work nobody
    is scheduled to do, and because no labor carries a technicianId the next
    availability pass reads the booking as unattributed shop capacity rather
    than a specific tech hold, so the error compounds.
    """

    BOOKING = {
        "service_id": "svc-1",
        "slot_start": "2026-01-19T09:00:00",
        "slot_end": "2026-01-19T10:00:00",
        "customer": {"firstName": "Test", "lastName": "User"},
        "vehicle": {"year": 2022, "make": "Toyota", "model": "Camry"},
    }

    def test_refuses_when_no_tech_can_be_selected(self, test_client, mock_shopmonkey_client):
        with patch("main.select_tech_by_priority", return_value=None):
            response = test_client.post("/book", json=self.BOOKING)

        assert response.status_code == 409
        assert "no longer available" in response.json()["detail"]

    def test_writes_nothing_when_no_tech_can_be_selected(self, test_client, mock_shopmonkey_client):
        """The refusal must happen BEFORE any record is created, or we leak an
        orphan customer/vehicle for a booking that never completes."""
        with patch("main.select_tech_by_priority", return_value=None):
            test_client.post("/book", json=self.BOOKING)

        mock_shopmonkey_client.create_appointment.assert_not_called()
        mock_shopmonkey_client.find_or_create_customer.assert_not_called()
        mock_shopmonkey_client.find_or_create_vehicle.assert_not_called()

    def test_normal_booking_still_succeeds(self, test_client):
        """The guard must not block bookings that do have a tech."""
        response = test_client.post("/book", json=self.BOOKING)
        assert response.status_code == 200
        assert response.json()["success"] is True


class TestNotificationFailureNeverLosesTheBooking:
    """Regression: everything after the appointment is created must not be able
    to fail the request. get_email_client() runs post-creation, so an exception
    there returned a 500 for a booking that actually succeeded - and the
    customer retried, double-booking a slot they already held.
    """

    BOOKING = {
        "service_id": "svc-1",
        "slot_start": "2026-01-19T09:00:00",
        "slot_end": "2026-01-19T10:00:00",
        "customer": {"firstName": "Test", "lastName": "User"},
        "vehicle": {"year": 2022, "make": "Toyota", "model": "Camry"},
    }

    def test_mailer_init_failure_still_confirms_the_booking(self, test_client):
        """A broken mailer is a staffing inconvenience, not a customer problem."""
        with patch("main.get_email_client", side_effect=ValueError("bad SMTP_PORT")):
            response = test_client.post("/book", json=self.BOOKING)

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["confirmation_number"]
