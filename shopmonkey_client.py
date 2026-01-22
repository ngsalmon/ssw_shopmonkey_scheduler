"""Async HTTP client for Shopmonkey API."""

import json
import os
import time
from typing import Any

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = structlog.get_logger(__name__)


class ShopmonkeyAPIError(Exception):
    """Base exception for Shopmonkey API errors."""

    def __init__(self, message: str, status_code: int | None = None, response_body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class ShopmonkeyTimeoutError(ShopmonkeyAPIError):
    """Exception raised when Shopmonkey API request times out."""

    def __init__(self, message: str = "Request to Shopmonkey API timed out"):
        super().__init__(message)


class ShopmonkeyNetworkError(ShopmonkeyAPIError):
    """Exception raised for network-related errors."""

    def __init__(self, message: str = "Network error communicating with Shopmonkey API"):
        super().__init__(message)


class ShopmonkeyClient:
    """Async client for interacting with Shopmonkey API v3."""

    def __init__(
        self,
        api_token: str | None = None,
        base_url: str | None = None,
        location_id: str | None = None,
        timeout: float = 30.0,
    ):
        self.api_token = api_token or os.getenv("SHOPMONKEY_API_TOKEN")
        self.base_url = (
            base_url
            or os.getenv("SHOPMONKEY_API_BASE_URL", "https://api.shopmonkey.cloud")
        ).rstrip("/")
        self.location_id = location_id or os.getenv("SHOPMONKEY_LOCATION_ID")
        self.timeout = timeout

        if not self.api_token:
            raise ValueError("SHOPMONKEY_API_TOKEN is required")

        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {self.api_token}",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout,
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @retry(
        retry=retry_if_exception_type((ShopmonkeyTimeoutError, ShopmonkeyNetworkError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        reraise=True,
    )
    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Make an HTTP request to the Shopmonkey API with retry logic.

        Retries on timeout and network errors with exponential backoff.
        Does not retry on 4xx client errors.
        """
        client = await self._get_client()
        start_time = time.monotonic()

        log = logger.bind(
            method=method,
            endpoint=endpoint,
            has_params=params is not None,
            has_body=json_data is not None,
        )

        try:
            response = await client.request(
                method=method,
                url=endpoint,
                params=params,
                json=json_data,
            )
            elapsed_ms = (time.monotonic() - start_time) * 1000

            log.debug(
                "shopmonkey_api_request",
                status_code=response.status_code,
                elapsed_ms=round(elapsed_ms, 2),
            )

            response.raise_for_status()
            return response.json()

        except httpx.TimeoutException as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            log.warning(
                "shopmonkey_api_timeout",
                elapsed_ms=round(elapsed_ms, 2),
                error=str(e),
            )
            raise ShopmonkeyTimeoutError(f"Request to {endpoint} timed out after {self.timeout}s") from e

        except httpx.NetworkError as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            log.warning(
                "shopmonkey_api_network_error",
                elapsed_ms=round(elapsed_ms, 2),
                error=str(e),
            )
            raise ShopmonkeyNetworkError(f"Network error calling {endpoint}: {str(e)}") from e

        except httpx.HTTPStatusError as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            log.error(
                "shopmonkey_api_error",
                status_code=e.response.status_code,
                elapsed_ms=round(elapsed_ms, 2),
                response_text=e.response.text[:500] if e.response.text else None,
            )
            raise ShopmonkeyAPIError(
                f"Shopmonkey API error: {e.response.status_code}",
                status_code=e.response.status_code,
                response_body=e.response.text,
            ) from e

    async def get_bookable_canned_services(self) -> list[dict[str, Any]]:
        """Fetch all canned services marked as bookable."""
        where_clause = json.dumps({"bookable": True})
        params = {"where": where_clause}

        if self.location_id:
            params["locationId"] = self.location_id

        result = await self._request("GET", "/v3/canned_service", params=params)
        return result.get("data", [])

    async def get_canned_service(self, service_id: str) -> dict[str, Any] | None:
        """Fetch a specific canned service by ID."""
        try:
            result = await self._request("GET", f"/v3/canned_service/{service_id}")
            return result.get("data")
        except ShopmonkeyAPIError as e:
            if e.status_code == 404:
                return None
            raise

    async def get_appointments_for_date(
        self, date_str: str, tech_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """
        Fetch appointments for a specific date.

        Args:
            date_str: Date in YYYY-MM-DD format
            tech_ids: Optional list of technician IDs to filter by
        """
        # Build date range for the full day
        start_date = f"{date_str}T00:00:00Z"
        end_date = f"{date_str}T23:59:59Z"

        where_clause: dict[str, Any] = {
            "startDate": {"$gte": start_date, "$lt": end_date}
        }

        params = {"where": json.dumps(where_clause)}

        if self.location_id:
            params["locationId"] = self.location_id

        result = await self._request("GET", "/v3/appointment", params=params)
        appointments = result.get("data", [])

        # Filter by tech IDs if provided
        if tech_ids:
            appointments = [
                appt
                for appt in appointments
                if appt.get("technicianId") in tech_ids
                or appt.get("userId") in tech_ids
            ]

        return appointments

    async def find_or_create_customer(
        self,
        first_name: str,
        last_name: str,
        email: str | None = None,
        phone: str | None = None,
    ) -> dict[str, Any]:
        """Find existing customer by email/phone or create new one."""
        # Try to find by email first
        if email:
            where_clause = json.dumps({"email": email})
            params = {"where": where_clause}
            if self.location_id:
                params["locationId"] = self.location_id

            result = await self._request("GET", "/v3/customer", params=params)
            customers = result.get("data", [])
            if customers:
                return customers[0]

        # Try to find by phone
        if phone:
            where_clause = json.dumps({"phone": phone})
            params = {"where": where_clause}
            if self.location_id:
                params["locationId"] = self.location_id

            result = await self._request("GET", "/v3/customer", params=params)
            customers = result.get("data", [])
            if customers:
                return customers[0]

        # Create new customer
        customer_data: dict[str, Any] = {
            "firstName": first_name,
            "lastName": last_name,
        }
        if email:
            customer_data["email"] = email
        if phone:
            customer_data["phone"] = phone
        if self.location_id:
            customer_data["locationId"] = self.location_id

        result = await self._request("POST", "/v3/customer", json_data=customer_data)
        return result.get("data", result)

    async def find_or_create_vehicle(
        self,
        customer_id: str,
        year: int,
        make: str,
        model: str,
        vin: str | None = None,
    ) -> dict[str, Any]:
        """Find existing vehicle or create new one for customer."""
        # Try to find existing vehicle by VIN
        if vin:
            where_clause = json.dumps({"vin": vin})
            params = {"where": where_clause}
            if self.location_id:
                params["locationId"] = self.location_id

            result = await self._request("GET", "/v3/vehicle", params=params)
            vehicles = result.get("data", [])
            if vehicles:
                return vehicles[0]

        # Try to find by customer + year/make/model
        where_clause = json.dumps(
            {
                "customerId": customer_id,
                "year": year,
                "make": make,
                "model": model,
            }
        )
        params = {"where": where_clause}
        if self.location_id:
            params["locationId"] = self.location_id

        result = await self._request("GET", "/v3/vehicle", params=params)
        vehicles = result.get("data", [])
        if vehicles:
            return vehicles[0]

        # Create new vehicle
        # Size is required by Shopmonkey API. Valid values: LightDuty, MediumDuty, HeavyDuty, Other
        vehicle_data: dict[str, Any] = {
            "customerId": customer_id,
            "year": year,
            "make": make,
            "model": model,
            "size": "LightDuty",
        }
        if vin:
            vehicle_data["vin"] = vin
        if self.location_id:
            vehicle_data["locationId"] = self.location_id

        result = await self._request("POST", "/v3/vehicle", json_data=vehicle_data)
        return result.get("data", result)

    async def create_appointment(
        self,
        customer_id: str,
        vehicle_id: str,
        start_date: str,
        end_date: str,
        title: str | None = None,
        notes: str | None = None,
        technician_id: str | None = None,
        color: str = "blue",
    ) -> dict[str, Any]:
        """Create a new appointment."""
        appointment_data: dict[str, Any] = {
            "customerId": customer_id,
            "vehicleId": vehicle_id,
            "startDate": start_date,
            "endDate": end_date,
            "color": color,  # Required by Shopmonkey API
            "name": title or "Online Booking",  # Required by Shopmonkey API
        }

        if notes:
            appointment_data["note"] = notes
        if technician_id:
            appointment_data["technicianId"] = technician_id
        if self.location_id:
            appointment_data["locationId"] = self.location_id

        result = await self._request(
            "POST", "/v3/appointment", json_data=appointment_data
        )
        return result.get("data", result)

    async def get_users(self) -> list[dict[str, Any]]:
        """Fetch all users (technicians)."""
        params = {}
        if self.location_id:
            params["locationId"] = self.location_id

        result = await self._request("GET", "/v3/user", params=params if params else None)
        return result.get("data", [])

    async def health_check(self) -> bool:
        """
        Perform a lightweight health check against the Shopmonkey API.

        Returns True if the API is reachable, False otherwise.
        """
        try:
            # Try to list users with a limit of 1 as a lightweight check
            await self._request("GET", "/v3/user", params={"limit": "1"})
            return True
        except (ShopmonkeyAPIError, ShopmonkeyTimeoutError, ShopmonkeyNetworkError):
            return False

    async def get_appointment(self, appointment_id: str) -> dict[str, Any] | None:
        """
        Fetch an appointment by ID.

        Args:
            appointment_id: The appointment ID to fetch.

        Returns:
            The appointment data, or None if not found.
        """
        try:
            result = await self._request("GET", f"/v3/appointment/{appointment_id}")
            return result.get("data")
        except ShopmonkeyAPIError as e:
            if e.status_code == 404:
                return None
            raise

    async def delete_appointment(self, appointment_id: str) -> bool:
        """
        Delete an appointment by ID.

        Args:
            appointment_id: The appointment ID to delete.

        Returns:
            True if deleted successfully, False if not found or deletion failed.
        """
        try:
            # Pass empty object since API rejects empty body with Content-Type header
            await self._request("DELETE", f"/v3/appointment/{appointment_id}", json_data={})
            logger.info("appointment_deleted", appointment_id=appointment_id)
            return True
        except ShopmonkeyAPIError as e:
            if e.status_code == 404:
                logger.warning("appointment_not_found_for_delete", appointment_id=appointment_id)
                return False
            if e.status_code == 403:
                logger.warning(
                    "appointment_delete_forbidden",
                    appointment_id=appointment_id,
                    message="API token may lack delete permission",
                )
                return False
            raise
