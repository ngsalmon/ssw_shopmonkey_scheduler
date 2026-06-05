"""Unit tests for FastAPI endpoints using TestClient."""

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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
        ):
            from main import app

            with TestClient(app) as client:
                yield client


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
        mock_shopmonkey_client.get_canned_service = AsyncMock(
            return_value=self._multiday_service()
        )
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
        mock_shopmonkey_client.get_canned_service = AsyncMock(
            return_value=self._multiday_service()
        )

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

        mock_shopmonkey_client.get_appointments_for_date = AsyncMock(
            side_effect=appointments_for
        )

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

        mock_shopmonkey_client.get_canned_service = AsyncMock(
            return_value=self._multiday_service()
        )
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
