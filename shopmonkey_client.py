"""Async HTTP client for Shopmonkey API."""

import asyncio
import json
import os
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

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

    def __init__(
        self, message: str, status_code: int | None = None, response_body: str | None = None
    ):
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


class ShopmonkeyRateLimitError(ShopmonkeyAPIError):
    """Raised on HTTP 429.

    Split out of the generic 4xx path because a rate limit is transient and
    must be retried, while every other 4xx is a request we got wrong and
    retrying only burns quota. Treating 429 as fatal is how a busy day turned
    into 502s on /availability, and - worse - how the per-appointment error
    handling in `get_busy_techs_for_appointments` used to drop a technician's
    assignments and report them as free.
    """

    def __init__(self, message: str = "Shopmonkey API rate limit exceeded", **kwargs: Any):
        super().__init__(message, **kwargs)


class ShopmonkeyClient:
    """Async client for interacting with Shopmonkey API v3."""

    # Max concurrent order reads during the availability labor walk. Shopmonkey
    # rate-limits aggressively; an unbounded fan-out over a full day trips it.
    ORDER_FETCH_CONCURRENCY = 5

    def __init__(
        self,
        api_token: str | None = None,
        base_url: str | None = None,
        location_id: str | None = None,
        timeout: float = 30.0,
        timezone: str | None = None,
    ):
        # Business timezone, used only to turn a YYYY-MM-DD into real local day
        # bounds for the appointment query. Mirrors config.yaml's `timezone`;
        # kept as a plain arg so the client stays config-file independent.
        self.timezone = timezone or os.getenv("BUSINESS_TIMEZONE") or "America/Chicago"
        self.api_token = api_token or os.getenv("SHOPMONKEY_API_TOKEN")
        self.base_url = (
            base_url or os.getenv("SHOPMONKEY_API_BASE_URL", "https://api.shopmonkey.cloud")
        ).rstrip("/")
        self.location_id = location_id or os.getenv("SHOPMONKEY_LOCATION_ID")
        self.timeout = timeout

        if not self.api_token:
            raise ValueError("SHOPMONKEY_API_TOKEN is required")

        self._client: httpx.AsyncClient | None = None
        self._active_user_ids_cache: set[str] | None = None
        self._active_user_ids_cache_expiry: float = 0.0
        self._active_user_ids_cache_ttl: float = 300.0

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
        retry=retry_if_exception_type(
            (ShopmonkeyTimeoutError, ShopmonkeyNetworkError, ShopmonkeyRateLimitError)
        ),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=16),
        reraise=True,
    )
    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | list[Any] | None = None,
    ) -> dict[str, Any]:
        """
        Make an HTTP request to the Shopmonkey API with retry logic.

        Retries on timeout, network errors and HTTP 429 with exponential
        backoff. Does not retry any other 4xx - those are our bug, not a
        transient condition.
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
            raise ShopmonkeyTimeoutError(
                f"Request to {endpoint} timed out after {self.timeout}s"
            ) from e

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
            if e.response.status_code == 429:
                log.warning(
                    "shopmonkey_api_rate_limited",
                    elapsed_ms=round(elapsed_ms, 2),
                )
                raise ShopmonkeyRateLimitError(
                    f"Shopmonkey API rate limit hit on {endpoint}",
                    status_code=429,
                    response_body=e.response.text,
                ) from e

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
            tech_ids: Ignored. The GET `where` param supports no tech/relation
                filter (11 syntaxes tried in probe_filter_appts_by_tech.py -
                all silently ignored), so we fetch the day and join to techs
                caller-side. Kept for backward compatibility.
                (`POST /v3/appointment/search` DOES support a `technicians`
                filter if per-tech fetching is ever needed.)

        Important Shopmonkey API quirks (verified 2026-05-20 against the live
        API in this shop's account):
            * The `where` filter uses MongoDB-like operators WITHOUT the `$`
              prefix. `{"startDate": {"$gte": ...}}` is silently ignored and
              returns ~100 unfiltered rows. `{"startDate": {"gte": ...}}`
              works and returns only matching rows.
            * Page size is hard-capped at 100. For a single day's slice this
              is fine (a busy day has well under 100 appointments).
            * `endDate` IS filterable alongside `startDate` (verified
              2026-08-18): a `{startDate: {lt: X}, endDate: {gt: Y}}` pair is
              honored as a conjunction, not silently dropped.

        The filter is a true OVERLAP test - `startDate < end_of_day AND
        endDate > start_of_day` - not `startDate` inside the day. Filtering on
        `startDate` alone made any entry that began before the queried date
        invisible on every later day it covered, so a single multi-day time-off
        block would have read as that technician being free. No such entry
        exists today (0 of 554 across Jun-Sep 2026; staff hand-build per-day
        "... cont." entries instead), which is exactly why this stayed latent.

        Bounds are the real LOCAL day converted to UTC, rather than the
        date's naive `Z` bounds. The old bounds were the local window
        19:00 yesterday - 18:59 today during CDT, which both pulled in the
        previous evening and cut off anything starting after 19:00 local.
        """
        tz = ZoneInfo(self.timezone)
        day = datetime.strptime(date_str, "%Y-%m-%d")
        day_start = datetime(day.year, day.month, day.day, tzinfo=tz)
        day_end = day_start + timedelta(days=1)

        start_date = day_start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        end_date = day_end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")

        where_clause: dict[str, Any] = {
            "startDate": {"lt": end_date},
            "endDate": {"gt": start_date},
        }

        params: dict[str, Any] = {"where": json.dumps(where_clause), "limit": "100"}

        if self.location_id:
            params["locationId"] = self.location_id

        result = await self._request("GET", "/v3/appointment", params=params)
        appointments = result.get("data", [])
        meta = result.get("meta") or {}

        if meta.get("hasMore"):
            # A single day exceeding 100 appointments would be extraordinary,
            # but log it so we know to add pagination if it ever happens.
            logger.warning(
                "appointment_list_has_more",
                date=date_str,
                returned=len(appointments),
                total=meta.get("total"),
            )

        return appointments

    async def get_busy_techs_for_appointments(
        self, appointments: list[dict[str, Any]]
    ) -> dict[str, set[str]]:
        """Resolve which technicians each appointment occupies.

        A tech is occupied by ANY calendar entry they're assigned to - a
        work order, a vacation block, a "shop cleaning" entry. The entry's
        title and whether it has a ticket behind it are irrelevant; only
        the assignment matters.

        Two independent sources, unioned:

        1. `appointment.technicians[]` - the assignment staff make on the
           calendar. The list endpoint returns it on every row (verified
           2026-08-07: 100/100), including time-off blocks and untickted
           work, which carry no `orderId` at all.
        2. Appointment → Order → Service.labors → technicianId, for
           `orderId`-bearing rows.

        Neither source subsumes the other (2026-08-07, 60-appointment
        sample: 19 disagreed - 11 where only the labor walk found a tech,
        3 where only `technicians[]` did), so we union them. Dropping
        either silently loses assignments and over-books.

        Returns {appointment_id: {tech_id, ...}}.

        Order fetches run in parallel behind a small semaphore. The cap is the
        point: an unbounded `gather` over a full day fired ~15-20 concurrent
        order reads and reliably tripped Shopmonkey's rate limiter.

        A failed order fetch is FATAL, not degraded. It used to be swallowed
        per-appointment and turned into an empty tech set, which reads exactly
        like "nobody is assigned to this ticket" - and roughly a fifth of
        appointments are known ONLY through the labor walk, so a single 429
        could hand a busy technician back to the scheduler as free and
        double-book them. Failing the whole availability request instead makes
        the caller return 502; the customer retries and sees correct times,
        which is strictly better than a confident wrong answer.
        """
        busy: dict[str, set[str]] = {}
        for appt in appointments:
            appt_id = appt.get("id")
            if not appt_id:
                continue
            busy[appt_id] = {t["id"] for t in (appt.get("technicians") or []) if t.get("id")}

        targets = [a for a in appointments if a.get("orderId") and a.get("id")]
        if not targets:
            return busy

        semaphore = asyncio.Semaphore(self.ORDER_FETCH_CONCURRENCY)

        async def fetch(appt: dict[str, Any]) -> tuple[str, set[str]]:
            appt_id = appt["id"]
            order_id = appt["orderId"]
            try:
                async with semaphore:
                    result = await self._request("GET", f"/v3/order/{order_id}/service")
            except ShopmonkeyAPIError as e:
                logger.error(
                    "fetch_order_services_failed",
                    order_id=order_id,
                    appointment_id=appt_id,
                    status_code=getattr(e, "status_code", None),
                )
                raise
            services = result.get("data") or []
            tech_ids: set[str] = set()
            for svc in services:
                for labor in svc.get("labors") or []:
                    tid = labor.get("technicianId")
                    if tid:
                        tech_ids.add(tid)
            return appt_id, tech_ids

        results = await asyncio.gather(*[fetch(a) for a in targets])
        for appt_id, tech_ids in results:
            busy[appt_id] |= tech_ids
        return busy

    @staticmethod
    def _normalize_phone(phone: str | None) -> str | None:
        """Return phone in E.164 format Shopmonkey accepts (e.g. "+15551234567").

        Strips formatting, defaults to a US country code when 10 digits are
        provided without one. Returns None for empty input. Returns the input
        unchanged when we can't confidently normalize (so we still send what
        the user provided).
        """
        if not phone:
            return None
        digits = "".join(ch for ch in phone if ch.isdigit())
        if not digits:
            return phone
        if phone.lstrip().startswith("+"):
            return "+" + digits
        if len(digits) == 10:
            return "+1" + digits
        if len(digits) == 11 and digits.startswith("1"):
            return "+" + digits
        return phone

    @staticmethod
    def _customer_matches(
        customer: dict[str, Any],
        email: str | None,
        phone: str | None,
    ) -> bool:
        """Return True when the customer's emails/phoneNumbers include a match.

        Shopmonkey stores emails and phone numbers as sub-resources on the
        customer record. The top-level `email`/`phone` fields are always null
        on list and detail responses, and `where: {"email": ...}` is silently
        ignored by the search endpoint, so we have to do the match in-memory.
        """
        if email:
            target = email.strip().lower()
            for entry in customer.get("emails") or []:
                if (entry.get("email") or "").strip().lower() == target:
                    return True
        if phone:
            target_digits = "".join(ch for ch in phone if ch.isdigit())
            for entry in customer.get("phoneNumbers") or []:
                entry_digits = "".join(ch for ch in (entry.get("number") or "") if ch.isdigit())
                if not entry_digits:
                    continue
                # Suffix matching exists to make "+1 816 555 1234", "816-555-1234"
                # and "8165551234" the same person, so it needs a full national
                # number to be meaningful. Below 10 digits the suffix is too weak
                # to establish identity: "5551234" would match a DIFFERENT
                # same-named customer's +19995551234, and an empty target
                # (a phone with no digits at all) would match every customer who
                # has any phone number, since "".endswith("") is True. Both
                # silently attach a booking to the wrong person - the same class
                # of misattribution this function was rewritten to prevent - so
                # short inputs require exact equality instead.
                if len(target_digits) >= 10:
                    if entry_digits.endswith(target_digits[-10:]):
                        return True
                elif target_digits and entry_digits == target_digits:
                    return True
        return False

    async def find_or_create_customer(
        self,
        first_name: str,
        last_name: str,
        email: str | None = None,
        phone: str | None = None,
    ) -> dict[str, Any]:
        """Find an existing customer or create a new one.

        Shopmonkey's `/v3/customer` `where` filter only honors top-level
        scalar fields. Emails and phone numbers live as sub-resources, so we
        can't filter on them directly (every value comes back as the same
        unfiltered page, which previously caused new bookings to attach to
        whichever customer sorted first by id - the misattachment Anne
        flagged on 2026-05-19).

        Instead: look up by firstName + lastName, then walk the returned
        records' `emails`/`phoneNumbers` sub-resources to verify the
        identity. Create a new record when no candidate matches.
        """
        where_clause = json.dumps({"firstName": first_name, "lastName": last_name})
        params: dict[str, Any] = {"where": where_clause}
        if self.location_id:
            params["locationId"] = self.location_id

        result = await self._request("GET", "/v3/customer", params=params)
        candidates = result.get("data", [])

        # Filter to records whose name actually matches (the where filter
        # might still return broader rows if Shopmonkey's behavior shifts).
        same_name = [
            c
            for c in candidates
            if (c.get("firstName") or "").strip().lower() == first_name.strip().lower()
            and (c.get("lastName") or "").strip().lower() == last_name.strip().lower()
        ]

        if email or phone:
            for candidate in same_name:
                if self._customer_matches(candidate, email, phone):
                    return candidate
        elif same_name:
            # No email/phone to verify - accept the first same-name match.
            return same_name[0]

        # No match; create a new customer with emails/phoneNumbers as
        # sub-resource arrays (top-level email/phone fields are silently
        # dropped by the API).
        customer_data: dict[str, Any] = {
            "firstName": first_name,
            "lastName": last_name,
            "customerType": "Customer",
        }
        if email:
            customer_data["emails"] = [{"email": email, "primary": True}]
        normalized_phone = self._normalize_phone(phone)
        if normalized_phone:
            customer_data["phoneNumbers"] = [{"number": normalized_phone, "primary": True}]
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
        order_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a new appointment, optionally linked to a repair order."""
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
            # PLURAL, and an array. `technicianId` (singular) is accepted with
            # a 200 and silently discarded - the appointment lands in the
            # calendar's "Unassigned" column and staff reassign it by hand.
            # Verified against prod 2026-08-18 with five payload shapes on
            # 2026-09-02: technicianId -> no link (with and without
            # customer/vehicle), technicians:[{id}] -> no link,
            # technicianIds:[id] -> link created, and PUT technicianIds on an
            # existing appointment -> link created (the repair path).
            # Sending both together also works, so the singular key is kept as
            # a hedge in case the API flips back.
            appointment_data["technicianId"] = technician_id
            appointment_data["technicianIds"] = [technician_id]
        if order_id:
            appointment_data["orderId"] = order_id
        if self.location_id:
            appointment_data["locationId"] = self.location_id

        result = await self._request("POST", "/v3/appointment", json_data=appointment_data)
        return result.get("data", result)

    async def get_workflow_statuses(self) -> list[dict[str, Any]]:
        """List all workflow status columns (Scheduled, In Progress, etc)."""
        params: dict[str, Any] = {}
        if self.location_id:
            params["locationId"] = self.location_id
        result = await self._request(
            "GET", "/v3/workflow_status", params=params if params else None
        )
        return result.get("data", [])

    async def get_workflow_status_id(self, name: str) -> str | None:
        """Return the workflow_status id matching the given name, or None."""
        statuses = await self.get_workflow_statuses()
        for s in statuses:
            if s.get("name") == name:
                return s.get("id")
        return None

    async def create_order(
        self,
        customer_id: str,
        vehicle_id: str,
        workflow_status_id: str,
        status: str = "Estimate",
        color: str | None = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        """Create a repair order in Shopmonkey.

        Pairs with `attach_services_to_order` and `create_appointment(order_id=...)`
        to mirror what the OOTB online scheduler creates: an order in the
        "Scheduled" workflow column plus an appointment linked via orderId.
        """
        order_data: dict[str, Any] = {
            "customerId": customer_id,
            "vehicleId": vehicle_id,
            "workflowStatusId": workflow_status_id,
            "status": status,
        }
        if color:
            order_data["color"] = color
        if name:
            order_data["name"] = name
        if self.location_id:
            order_data["locationId"] = self.location_id
        result = await self._request("POST", "/v3/order", json_data=order_data)
        return result.get("data", result)

    async def attach_services_to_order(
        self,
        order_id: str,
        services: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Attach one or more service line items to an order.

        Each service dict should include at minimum `cannedServiceId` and
        `name`, and ideally a `labors` array copied from the canned service so
        the order's calculated labor cents are populated. POSTing without
        labors leaves the line item at $0 - functional but missing pricing.
        """
        result = await self._request("POST", f"/v3/order/{order_id}/service", json_data=services)
        data = result.get("data", result)
        if isinstance(data, dict):
            return data.get("services", [])
        return data

    async def get_users(self) -> list[dict[str, Any]]:
        """Fetch all users (technicians)."""
        params = {}
        if self.location_id:
            params["locationId"] = self.location_id

        result = await self._request("GET", "/v3/user", params=params if params else None)
        return result.get("data", [])

    async def get_active_user_ids(self) -> set[str]:
        """Return IDs of active Shopmonkey users (cached for 5 minutes)."""
        now = time.monotonic()
        if self._active_user_ids_cache is not None and now < self._active_user_ids_cache_expiry:
            return self._active_user_ids_cache

        users = await self.get_users()
        active_ids = {u["id"] for u in users if u.get("active") and u.get("id")}
        self._active_user_ids_cache = active_ids
        self._active_user_ids_cache_expiry = now + self._active_user_ids_cache_ttl
        return active_ids

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
