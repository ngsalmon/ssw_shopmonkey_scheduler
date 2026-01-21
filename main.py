"""FastAPI application for Shopmonkey scheduling APIs."""

import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field

from availability import (
    calculate_available_slots,
    get_business_hours,
    get_service_duration_minutes,
    is_slot_available,
    load_config,
)
from sheets_client import SheetsClient
from shopmonkey_client import ShopmonkeyClient

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Global instances
shopmonkey_client: ShopmonkeyClient | None = None
sheets_client: SheetsClient | None = None
config: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize and cleanup resources."""
    global shopmonkey_client, sheets_client, config

    logger.info("Starting Shopmonkey Scheduling API")

    # Load configuration
    config_path = os.getenv("CONFIG_PATH", "config.yaml")
    config = load_config(config_path)
    logger.info("Loaded configuration from %s", config_path)

    # Initialize clients
    shopmonkey_client = ShopmonkeyClient()
    sheets_client = SheetsClient()
    logger.info("Initialized Shopmonkey and Sheets clients")

    yield

    # Cleanup
    logger.info("Shutting down Shopmonkey Scheduling API")
    if shopmonkey_client:
        await shopmonkey_client.close()
        logger.debug("Closed Shopmonkey client")


app = FastAPI(
    title="Shopmonkey Scheduling API",
    description="APIs for listing bookable services, checking availability, and booking appointments",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware for embedded widget support
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure specific origins in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files directory
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


# Request/Response Models
class ServiceResponse(BaseModel):
    id: str
    name: str
    totalCents: int | None = None
    bookable: bool = True
    category: str | None = None


class ServicesListResponse(BaseModel):
    services: list[ServiceResponse]


class SlotResponse(BaseModel):
    start: str
    end: str
    available_techs: int


class AvailabilityResponse(BaseModel):
    service_id: str
    date: str
    duration_minutes: int
    business_hours_close: str
    slots: list[SlotResponse]


class CustomerInfo(BaseModel):
    firstName: str
    lastName: str
    email: EmailStr | None = None
    phone: str | None = None


class VehicleInfo(BaseModel):
    year: int = Field(..., ge=1900, le=2100)
    make: str
    model: str
    vin: str | None = None


class BookingRequest(BaseModel):
    service_id: str
    slot_start: str  # ISO format: 2026-01-20T09:00:00
    slot_end: str  # ISO format: 2026-01-20T10:00:00
    customer: CustomerInfo
    vehicle: VehicleInfo


class BookingResponse(BaseModel):
    success: bool
    appointment_id: str
    confirmation_number: str


# Label to Tech/Dept column mapping
# Add mappings here if Shopmonkey labels differ from Tech/Dept column names
LABEL_TO_DEPARTMENT: dict[str, str] = {}


def get_department_from_service(service: dict[str, Any]) -> str | None:
    """
    Extract department from Shopmonkey service labels.

    Uses the first label on the service and maps it to the Tech/Dept column name.
    """
    labels = service.get("labels", [])
    if not labels:
        return None

    label_name = labels[0].get("name", "")
    if not label_name:
        return None

    # Map label to Tech/Dept column name (or use as-is if no mapping needed)
    return LABEL_TO_DEPARTMENT.get(label_name, label_name)


# API Endpoints
@app.get("/services", response_model=ServicesListResponse)
async def list_services():
    """
    List all bookable canned services from Shopmonkey.

    Returns a list of services that are marked as bookable, including
    their ID, name, and pricing information.
    """
    logger.debug("Fetching bookable services")
    if not shopmonkey_client:
        logger.error("Shopmonkey client not initialized")
        raise HTTPException(status_code=500, detail="Shopmonkey client not initialized")

    try:
        services = await shopmonkey_client.get_bookable_canned_services()
        logger.info("Retrieved %d bookable services", len(services))

        def get_category(svc: dict) -> str | None:
            labels = svc.get("labels", [])
            if labels and labels[0].get("name"):
                return labels[0].get("name")
            return None

        return ServicesListResponse(
            services=[
                ServiceResponse(
                    id=svc.get("id", ""),
                    name=svc.get("name", ""),
                    totalCents=svc.get("totalCents") or svc.get("priceCents"),
                    bookable=True,
                    category=get_category(svc),
                )
                for svc in services
            ]
        )
    except Exception as e:
        logger.exception("Error fetching services")
        raise HTTPException(status_code=500, detail=f"Error fetching services: {str(e)}")


@app.get("/availability", response_model=AvailabilityResponse)
async def get_availability(
    service_id: str = Query(..., description="The ID of the service to check availability for"),
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
):
    """
    Get available appointment slots for a specific service and date.

    This endpoint:
    1. Gets department from the service's Shopmonkey label
    2. Finds all technicians qualified for that department
    3. Checks existing Shopmonkey appointments for those techs
    4. Returns available time slots where at least one tech is free
    """
    logger.debug("Checking availability for service_id=%s, date=%s", service_id, date)
    if not shopmonkey_client or not sheets_client:
        logger.error("Clients not initialized")
        raise HTTPException(status_code=500, detail="Clients not initialized")

    # Parse and validate date
    try:
        target_date = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        logger.warning("Invalid date format: %s", date)
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

    try:
        # First, get the service from Shopmonkey to get its name
        service = await shopmonkey_client.get_canned_service(service_id)
        if not service:
            logger.warning("Service not found: %s", service_id)
            raise HTTPException(
                status_code=404,
                detail=f"Service {service_id} not found in Shopmonkey",
            )

        service_name = service.get("name", "")
        logger.debug("Found service: %s", service_name)

        # Get department from Shopmonkey service label
        department = get_department_from_service(service)
        if not department:
            logger.warning("Service '%s' has no department label", service_name)
            raise HTTPException(
                status_code=404,
                detail=f"Service '{service_name}' has no department label in Shopmonkey",
            )

        logger.debug("Department for service: %s", department)

        # Get qualified technicians for this department
        qualified_techs = sheets_client.get_techs_for_department(department)
        if not qualified_techs:
            logger.warning("No technicians found for department: %s", department)
            raise HTTPException(
                status_code=404,
                detail=f"No technicians found for department: {department}",
            )

        tech_ids = [t["tech_id"] for t in qualified_techs]
        logger.debug("Found %d qualified techs: %s", len(tech_ids), [t["tech_name"] for t in qualified_techs])

        # Get existing appointments for the date
        appointments = await shopmonkey_client.get_appointments_for_date(date, tech_ids)
        logger.debug("Found %d existing appointments for date", len(appointments))

        # Get service duration
        slot_duration = get_service_duration_minutes(service, config.get("default_slot_duration_minutes", 60))
        logger.debug("Service duration: %d minutes", slot_duration)

        # For multi-day services, fetch appointments for upcoming days
        future_appointments: dict[str, list] = {}
        if slot_duration > 300:  # Only fetch future days for services > 5 hours
            # Fetch up to 5 future business days
            check_date = target_date
            for _ in range(5):
                check_date = check_date + timedelta(days=1)
                date_str = check_date.strftime("%Y-%m-%d")
                try:
                    future_appts = await shopmonkey_client.get_appointments_for_date(
                        date_str, tech_ids
                    )
                    future_appointments[date_str] = future_appts
                except Exception:
                    pass  # Continue even if future date fetch fails

        # Calculate available slots
        available_slots = calculate_available_slots(
            date=target_date,
            tech_ids=tech_ids,
            appointments=appointments,
            config=config,
            slot_duration_minutes=slot_duration,
            future_appointments=future_appointments,
        )

        # Get business hours for the close time
        business_hours = get_business_hours(config, target_date)
        close_time = business_hours.close_time.strftime("%H:%M") if business_hours.is_open else "18:00"

        logger.info(
            "Availability check: service=%s, date=%s, department=%s, duration=%d min, slots=%d",
            service_name, date, department, slot_duration, len(available_slots)
        )

        return AvailabilityResponse(
            service_id=service_id,
            date=date,
            duration_minutes=slot_duration,
            business_hours_close=close_time,
            slots=[
                SlotResponse(
                    start=slot.start.strftime("%H:%M"),
                    end=slot.end.strftime("%H:%M"),
                    available_techs=slot.available_techs,
                )
                for slot in available_slots
            ],
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error checking availability for service %s", service_id)
        raise HTTPException(status_code=500, detail=f"Error checking availability: {str(e)}")


@app.post("/book", response_model=BookingResponse)
async def book_appointment(request: BookingRequest):
    """
    Book an appointment for a service at a specific time slot.

    This endpoint:
    1. Validates the slot is still available
    2. Finds or creates the customer in Shopmonkey
    3. Finds or creates the vehicle in Shopmonkey
    4. Creates the appointment in Shopmonkey
    """
    logger.info(
        "Booking request: service=%s, slot=%s to %s, customer=%s %s",
        request.service_id, request.slot_start, request.slot_end,
        request.customer.firstName, request.customer.lastName
    )
    if not shopmonkey_client or not sheets_client:
        logger.error("Clients not initialized")
        raise HTTPException(status_code=500, detail="Clients not initialized")

    try:
        # Parse slot times
        slot_start_dt = datetime.fromisoformat(request.slot_start.replace("Z", "+00:00"))
        slot_end_dt = datetime.fromisoformat(request.slot_end.replace("Z", "+00:00"))
        date_str = slot_start_dt.strftime("%Y-%m-%d")

        # First, get the service from Shopmonkey to get its name
        service = await shopmonkey_client.get_canned_service(request.service_id)
        if not service:
            logger.warning("Service not found: %s", request.service_id)
            raise HTTPException(
                status_code=404,
                detail=f"Service {request.service_id} not found in Shopmonkey",
            )

        service_name = service.get("name", "Service")

        # Get department from Shopmonkey service label
        department = get_department_from_service(service)
        if not department:
            logger.warning("Service '%s' has no department label", service_name)
            raise HTTPException(
                status_code=404,
                detail=f"Service '{service_name}' has no department label in Shopmonkey",
            )

        qualified_techs = sheets_client.get_techs_for_department(department)
        if not qualified_techs:
            logger.warning("No technicians found for department: %s", department)
            raise HTTPException(
                status_code=404,
                detail=f"No technicians found for department: {department}",
            )

        tech_ids = [t["tech_id"] for t in qualified_techs]

        # Re-check availability
        appointments = await shopmonkey_client.get_appointments_for_date(date_str, tech_ids)
        is_available, available_tech_ids = is_slot_available(
            date=slot_start_dt,
            slot_start=slot_start_dt.time(),
            slot_end=slot_end_dt.time(),
            tech_ids=tech_ids,
            appointments=appointments,
        )

        if not is_available:
            logger.warning("Slot no longer available: %s to %s", request.slot_start, request.slot_end)
            raise HTTPException(
                status_code=409,
                detail="The selected time slot is no longer available",
            )

        # Find or create customer
        logger.debug("Finding or creating customer: %s %s", request.customer.firstName, request.customer.lastName)
        customer = await shopmonkey_client.find_or_create_customer(
            first_name=request.customer.firstName,
            last_name=request.customer.lastName,
            email=request.customer.email,
            phone=request.customer.phone,
        )
        customer_id = customer.get("id")
        if not customer_id:
            logger.error("Failed to create customer")
            raise HTTPException(status_code=500, detail="Failed to create customer")
        logger.debug("Customer ID: %s", customer_id)

        # Find or create vehicle
        logger.debug("Finding or creating vehicle: %s %s %s", request.vehicle.year, request.vehicle.make, request.vehicle.model)
        vehicle = await shopmonkey_client.find_or_create_vehicle(
            customer_id=customer_id,
            year=request.vehicle.year,
            make=request.vehicle.make,
            model=request.vehicle.model,
            vin=request.vehicle.vin,
        )
        vehicle_id = vehicle.get("id")
        if not vehicle_id:
            logger.error("Failed to create vehicle")
            raise HTTPException(status_code=500, detail="Failed to create vehicle")
        logger.debug("Vehicle ID: %s", vehicle_id)

        # Create appointment - assign to first available tech
        assigned_tech_id = available_tech_ids[0] if available_tech_ids else None
        logger.debug("Assigning to technician: %s", assigned_tech_id)

        appointment = await shopmonkey_client.create_appointment(
            customer_id=customer_id,
            vehicle_id=vehicle_id,
            start_date=request.slot_start,
            end_date=request.slot_end,
            title=f"Online Booking: {service_name}",
            notes=f"Service requested: {service_name}\nService ID: {request.service_id}\nBooked online via scheduling API.",
            technician_id=assigned_tech_id,
        )

        appointment_id = appointment.get("id", "")

        # Generate confirmation number
        date_part = slot_start_dt.strftime("%Y%m%d")
        unique_part = uuid.uuid4().hex[:6].upper()
        confirmation_number = f"SM-{date_part}-{unique_part}"

        logger.info(
            "Booking successful: appointment_id=%s, confirmation=%s, service=%s, tech=%s",
            appointment_id, confirmation_number, service_name, assigned_tech_id
        )

        return BookingResponse(
            success=True,
            appointment_id=appointment_id,
            confirmation_number=confirmation_number,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error booking appointment for service %s", request.service_id)
        raise HTTPException(status_code=500, detail=f"Error booking appointment: {str(e)}")


@app.get("/schedule")
async def schedule_page():
    """Serve the scheduling widget page."""
    widget_path = os.path.join(static_dir, "widget.html")
    if not os.path.exists(widget_path):
        raise HTTPException(status_code=404, detail="Scheduling widget not found")
    return FileResponse(widget_path, media_type="text/html")


@app.get("/health")
async def health_check():
    """Health check endpoint for Cloud Run."""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
