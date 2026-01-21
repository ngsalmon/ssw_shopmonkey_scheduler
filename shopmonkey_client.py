"""Async HTTP client for Shopmonkey API."""

import json
import os
from typing import Any

import httpx


class ShopmonkeyClient:
    """Async client for interacting with Shopmonkey API v3."""

    def __init__(
        self,
        api_token: str | None = None,
        base_url: str | None = None,
        location_id: str | None = None,
    ):
        self.api_token = api_token or os.getenv("SHOPMONKEY_API_TOKEN")
        self.base_url = (
            base_url
            or os.getenv("SHOPMONKEY_API_BASE_URL", "https://api.shopmonkey.cloud")
        ).rstrip("/")
        self.location_id = location_id or os.getenv("SHOPMONKEY_LOCATION_ID")

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
                timeout=30.0,
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = await self._get_client()
        response = await client.request(
            method=method,
            url=endpoint,
            params=params,
            json=json_data,
        )
        response.raise_for_status()
        return response.json()

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
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
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
        vehicle_data: dict[str, Any] = {
            "customerId": customer_id,
            "year": year,
            "make": make,
            "model": model,
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
    ) -> dict[str, Any]:
        """Create a new appointment."""
        appointment_data: dict[str, Any] = {
            "customerId": customer_id,
            "vehicleId": vehicle_id,
            "startDate": start_date,
            "endDate": end_date,
        }

        if title:
            appointment_data["title"] = title
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
