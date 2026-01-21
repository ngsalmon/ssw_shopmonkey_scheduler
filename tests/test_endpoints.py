"""Unit tests for FastAPI endpoints using TestClient."""

import pytest
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def mock_shopmonkey_client():
    """Create a mock ShopmonkeyClient."""
    client = AsyncMock()
    client.get_bookable_canned_services = AsyncMock(return_value=[
        {"id": "svc-1", "name": "Window Tint", "totalCents": 15000, "labels": [{"name": "Window Tint"}]},
        {"id": "svc-2", "name": "Paint Protection Film", "totalCents": 50000, "labels": [{"name": "Vinyl"}]},
    ])
    client.get_canned_service = AsyncMock(return_value={
        "id": "svc-1",
        "name": "Window Tint",
        "totalCents": 15000,
        "labels": [{"name": "Window Tint"}],
        "estimatedDuration": 60,
    })
    client.get_appointments_for_date = AsyncMock(return_value=[])
    client.find_or_create_customer = AsyncMock(return_value={"id": "cust-123"})
    client.find_or_create_vehicle = AsyncMock(return_value={"id": "veh-456"})
    client.create_appointment = AsyncMock(return_value={"id": "appt-789"})
    client.close = AsyncMock()
    return client


@pytest.fixture
def mock_sheets_client():
    """Create a mock SheetsClient."""
    client = MagicMock()
    client.get_techs_for_department = MagicMock(return_value=[
        {"tech_id": "tech-1", "tech_name": "John Doe"},
        {"tech_id": "tech-2", "tech_name": "Jane Smith"},
    ])
    client.get_all_departments = MagicMock(return_value=["Window Tint", "Vinyl", "Detail"])
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
    with patch("main.ShopmonkeyClient", return_value=mock_shopmonkey_client), \
         patch("main.SheetsClient", return_value=mock_sheets_client), \
         patch("main.load_config", return_value=mock_config):
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
        mock_shopmonkey_client.get_canned_service = AsyncMock(return_value={
            "id": "svc-no-label",
            "name": "Unlabeled Service",
            "labels": [],
        })
        response = test_client.get("/availability?service_id=svc-no-label&date=2026-01-19")
        assert response.status_code == 404
        assert "no department label" in response.json()["detail"]

    def test_no_techs_for_department_returns_404(self, test_client, mock_sheets_client):
        """Should return 404 when no techs for department."""
        mock_sheets_client.get_techs_for_department = MagicMock(return_value=[])
        response = test_client.get("/availability?service_id=svc-1&date=2026-01-19")
        assert response.status_code == 404
        assert "No technicians found" in response.json()["detail"]


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
        """Should return 409 when slot is no longer available."""
        # First call is during availability check in book endpoint
        mock_shopmonkey_client.get_appointments_for_date = AsyncMock(return_value=[
            {
                "technicianId": "tech-1",
                "startDate": "2026-01-19T09:00:00Z",
                "endDate": "2026-01-19T10:00:00Z",
            },
            {
                "technicianId": "tech-2",
                "startDate": "2026-01-19T09:00:00Z",
                "endDate": "2026-01-19T10:00:00Z",
            },
        ])
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
