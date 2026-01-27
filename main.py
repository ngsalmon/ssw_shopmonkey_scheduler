"""FastAPI application for Shopmonkey scheduling APIs."""

import asyncio
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Annotated, Any

import structlog
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field, field_validator

from availability import (
    calculate_available_slots,
    get_buffer_minutes,
    get_business_hours,
    get_service_duration_minutes,
    is_slot_available,
    load_config,
    validate_config,
)
from email_client import BookingDetails, get_email_client
from sheets_client import SheetsClient
from shopmonkey_client import ShopmonkeyClient, ShopmonkeyAPIError

# Load environment variables
load_dotenv()

# Configure structlog for JSON output in production
def configure_logging():
    """Configure structured logging with JSON output."""
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    # Use JSON in production, pretty console output in development
    if os.getenv("ENVIRONMENT", "development") == "production":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            int(os.getenv("LOG_LEVEL", "20"))  # INFO = 20
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


configure_logging()
logger = structlog.get_logger(__name__)

# Global instances
shopmonkey_client: ShopmonkeyClient | None = None
sheets_client: SheetsClient | None = None
config: dict[str, Any] = {}

# Booking lock to prevent race conditions (in-process only)
# NOTE: For multi-instance deployments, use a distributed lock (e.g., Redis)
booking_lock = asyncio.Lock()

# Round-robin tracker for tech assignment within same priority level
# Key: department name, Value: index of last assigned tech within that priority group
# NOTE: Resets on server restart. For persistence, use Redis or database.
round_robin_tracker: dict[str, dict[int, int]] = {}


def select_tech_by_priority(
    qualified_techs: list[dict],
    available_tech_ids: list[str],
    department: str,
) -> str | None:
    """
    Select a technician based on priority and round-robin within same priority.

    Args:
        qualified_techs: List of {tech_id, tech_name, priority} sorted by priority
        available_tech_ids: List of tech IDs that are available for the slot
        department: Department name for round-robin tracking

    Returns:
        Selected tech_id, or None if no techs available
    """
    # Filter to only available techs, preserving priority order
    available_techs = [t for t in qualified_techs if t["tech_id"] in available_tech_ids]

    if not available_techs:
        return None

    # Find the highest priority (lowest number) among available techs
    highest_priority = available_techs[0]["priority"]

    # Get all techs at that priority level
    same_priority_techs = [t for t in available_techs if t["priority"] == highest_priority]

    if len(same_priority_techs) == 1:
        # Only one tech at this priority, no round-robin needed
        return same_priority_techs[0]["tech_id"]

    # Round-robin among techs with same priority
    if department not in round_robin_tracker:
        round_robin_tracker[department] = {}

    dept_tracker = round_robin_tracker[department]
    last_index = dept_tracker.get(highest_priority, -1)

    # Find next tech in rotation
    next_index = (last_index + 1) % len(same_priority_techs)
    selected_tech = same_priority_techs[next_index]

    # Update tracker
    dept_tracker[highest_priority] = next_index

    logger.debug(
        "tech_selected_by_priority",
        department=department,
        priority=highest_priority,
        selected_tech=selected_tech["tech_name"],
        round_robin_index=next_index,
    )

    return selected_tech["tech_id"]


# API Key authentication
API_KEY = os.getenv("API_KEY")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str | None = Security(api_key_header)) -> str | None:
    """
    Verify API key if one is configured.

    If API_KEY is not set in environment, authentication is disabled (backwards compatible).
    If API_KEY is set, requests must include a valid X-API-Key header.
    """
    if not API_KEY:
        # No API key configured - authentication disabled
        return None

    if not api_key:
        logger.warning("api_key_missing")
        raise HTTPException(
            status_code=401,
            detail="API key required. Include X-API-Key header.",
        )

    if api_key != API_KEY:
        logger.warning("api_key_invalid")
        raise HTTPException(
            status_code=401,
            detail="Invalid API key",
        )

    return api_key


# Type alias for authenticated endpoints
ApiKeyDep = Annotated[str | None, Depends(verify_api_key)]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize and cleanup resources."""
    global shopmonkey_client, sheets_client, config

    logger.info("application_starting")

    # Load and validate configuration
    config_path = os.getenv("CONFIG_PATH", "config.yaml")
    try:
        config = load_config(config_path)
        validate_config(config)
        logger.info("config_loaded", config_path=config_path)
    except FileNotFoundError:
        logger.error("config_file_not_found", config_path=config_path)
        raise RuntimeError(f"Configuration file not found: {config_path}")
    except ValueError as e:
        logger.error("config_validation_failed", error=str(e))
        raise RuntimeError(f"Invalid configuration: {e}")

    # Initialize clients
    try:
        shopmonkey_client = ShopmonkeyClient()
        sheets_client = SheetsClient()
        logger.info("clients_initialized")
    except ValueError as e:
        logger.error("client_initialization_failed", error=str(e))
        raise RuntimeError(f"Failed to initialize clients: {e}")

    yield

    # Cleanup
    logger.info("application_shutting_down")
    if shopmonkey_client:
        await shopmonkey_client.close()
        logger.debug("shopmonkey_client_closed")


app = FastAPI(
    title="Shopmonkey Scheduling API",
    description="APIs for listing bookable services, checking availability, and booking appointments",
    version="1.0.0",
    lifespan=lifespan,
)


# CORS middleware configuration
def get_cors_origins() -> list[str]:
    """Get allowed CORS origins from environment."""
    origins_str = os.getenv("ALLOWED_ORIGINS", "")
    if not origins_str:
        return []
    if origins_str == "*":
        return ["*"]
    return [origin.strip() for origin in origins_str.split(",") if origin.strip()]


allowed_origins = get_cors_origins()
if allowed_origins:
    # Only add CORS middleware if origins are configured
    cors_config = {
        "allow_origins": allowed_origins,
        "allow_methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["*"],
    }
    # Don't allow credentials with wildcard origins (security issue)
    if allowed_origins != ["*"]:
        cors_config["allow_credentials"] = True

    app.add_middleware(CORSMiddleware, **cors_config)
    logger.info("cors_configured", origins=allowed_origins)
else:
    logger.info("cors_disabled", reason="ALLOWED_ORIGINS not set")


# Request logging middleware
@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """Log all HTTP requests with timing and request ID."""
    request_id = str(uuid.uuid4())[:8]
    start_time = time.monotonic()

    # Bind request_id to structlog context
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id)

    # Add request_id to response headers
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id

    elapsed_ms = (time.monotonic() - start_time) * 1000
    logger.info(
        "http_request",
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        elapsed_ms=round(elapsed_ms, 2),
    )

    return response


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
    laborHours: float | None = None


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
    firstName: str = Field(..., min_length=1, max_length=100)
    lastName: str = Field(..., min_length=1, max_length=100)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=20)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str | None) -> str | None:
        if v is None:
            return v
        # Remove common formatting characters and validate
        cleaned = re.sub(r"[\s\-\(\)\.]", "", v)
        if cleaned and not re.match(r"^\+?\d{7,15}$", cleaned):
            raise ValueError("Invalid phone number format")
        return v


class VehicleInfo(BaseModel):
    year: int = Field(..., ge=1900, le=2100)
    make: str = Field(..., min_length=1, max_length=50)
    model: str = Field(..., min_length=1, max_length=50)
    vin: str | None = Field(None, max_length=17)


class BookingRequest(BaseModel):
    service_id: str = Field(..., max_length=100)
    slot_start: str  # ISO format: 2026-01-20T09:00:00
    slot_end: str  # ISO format: 2026-01-20T10:00:00
    customer: CustomerInfo
    vehicle: VehicleInfo


class BookingResponse(BaseModel):
    success: bool
    appointment_id: str
    confirmation_number: str


class HealthResponse(BaseModel):
    status: str


class ReadinessResponse(BaseModel):
    status: str
    shopmonkey: str
    sheets: str
    sheets_cache: dict | None = None


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


async def get_qualified_techs_for_service(
    service_id: str,
) -> tuple[dict[str, Any], str, list[dict[str, str]]]:
    """
    Get service details and qualified technicians.

    This is a helper that consolidates the common logic used by both
    /availability and /book endpoints.

    Args:
        service_id: The Shopmonkey service ID

    Returns:
        Tuple of (service, department, qualified_techs)

    Raises:
        HTTPException: On various error conditions (404, 500)
    """
    if not shopmonkey_client or not sheets_client:
        logger.error("clients_not_initialized")
        raise HTTPException(status_code=500, detail="Service temporarily unavailable")

    # Get the service from Shopmonkey
    try:
        service = await shopmonkey_client.get_canned_service(service_id)
    except ShopmonkeyAPIError as e:
        logger.error("shopmonkey_api_error", service_id=service_id, error=str(e))
        raise HTTPException(status_code=502, detail="Unable to reach scheduling service")

    if not service:
        logger.warning("service_not_found", service_id=service_id)
        raise HTTPException(
            status_code=404,
            detail=f"Service not found",
        )

    service_name = service.get("name", "")
    logger.debug("service_found", service_id=service_id, service_name=service_name)

    # Get department from Shopmonkey service label
    department = get_department_from_service(service)
    if not department:
        logger.warning("service_no_department", service_id=service_id, service_name=service_name)
        raise HTTPException(
            status_code=404,
            detail=f"Service configuration incomplete",
        )

    logger.debug("department_resolved", department=department)

    # Get qualified technicians for this department
    try:
        qualified_techs = await sheets_client.get_techs_for_department(department)
    except Exception as e:
        logger.error("sheets_api_error", department=department, error=str(e))
        raise HTTPException(status_code=502, detail="Unable to reach scheduling service")

    if not qualified_techs:
        logger.warning("no_techs_for_department", department=department)
        raise HTTPException(
            status_code=404,
            detail=f"No availability for this service",
        )

    logger.debug(
        "qualified_techs_found",
        department=department,
        tech_count=len(qualified_techs),
    )

    return service, department, qualified_techs


# API Endpoints
@app.get("/services", response_model=ServicesListResponse)
async def list_services(_: ApiKeyDep):
    """
    List all bookable canned services from Shopmonkey.

    Returns a list of services that are marked as bookable, including
    their ID, name, and pricing information.
    """
    logger.debug("fetching_bookable_services")
    if not shopmonkey_client:
        logger.error("shopmonkey_client_not_initialized")
        raise HTTPException(status_code=500, detail="Service temporarily unavailable")

    try:
        services = await shopmonkey_client.get_bookable_canned_services()
        logger.info("services_fetched", count=len(services))

        def get_category(svc: dict) -> str | None:
            labels = svc.get("labels", [])
            if labels and labels[0].get("name"):
                return labels[0].get("name")
            return None

        def get_labor_hours(svc: dict) -> float | None:
            labors = svc.get("labors", [])
            if not labors:
                return None
            total_hours = sum(labor.get("hours", 0) for labor in labors)
            return round(total_hours, 1) if total_hours > 0 else None

        return ServicesListResponse(
            services=[
                ServiceResponse(
                    id=svc.get("id", ""),
                    name=svc.get("name", ""),
                    totalCents=svc.get("totalCents") or svc.get("priceCents"),
                    bookable=True,
                    category=get_category(svc),
                    laborHours=get_labor_hours(svc),
                )
                for svc in services
            ]
        )
    except ShopmonkeyAPIError as e:
        logger.error("shopmonkey_api_error", error=str(e))
        raise HTTPException(status_code=502, detail="Unable to reach scheduling service")
    except Exception as e:
        logger.exception("unexpected_error_fetching_services")
        raise HTTPException(status_code=500, detail="An unexpected error occurred")


@app.get("/availability", response_model=AvailabilityResponse)
async def get_availability(
    _: ApiKeyDep,
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
    logger.debug("checking_availability", service_id=service_id, date=date)

    # Parse and validate date
    try:
        target_date = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        logger.warning("invalid_date_format", date=date)
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

    try:
        # Get service and qualified techs using shared helper
        service, department, qualified_techs = await get_qualified_techs_for_service(service_id)
        tech_ids = [t["tech_id"] for t in qualified_techs]

        # Get existing appointments for the date
        appointments = await shopmonkey_client.get_appointments_for_date(date, tech_ids)
        logger.debug("existing_appointments_fetched", count=len(appointments))

        # Get service duration (labor time + any buffer for cure time, etc.)
        labor_duration = get_service_duration_minutes(service, config.get("default_slot_duration_minutes", 60))
        buffer_minutes = get_buffer_minutes(service, config)
        slot_duration = labor_duration + buffer_minutes
        logger.debug("service_duration", labor_minutes=labor_duration, buffer_minutes=buffer_minutes, total_minutes=slot_duration)

        # For multi-day services, fetch appointments for upcoming days
        future_appointments: dict[str, list] = {}
        if slot_duration > 300:  # Only fetch future days for services > 5 hours
            check_date = target_date
            for _ in range(5):
                check_date = check_date + timedelta(days=1)
                date_str = check_date.strftime("%Y-%m-%d")
                try:
                    future_appts = await shopmonkey_client.get_appointments_for_date(
                        date_str, tech_ids
                    )
                    future_appointments[date_str] = future_appts
                except ShopmonkeyAPIError:
                    logger.warning("future_appointments_fetch_failed", date=date_str)
                    # Continue even if future date fetch fails

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
            "availability_checked",
            service_id=service_id,
            date=date,
            department=department,
            duration_minutes=slot_duration,
            slot_count=len(available_slots),
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
        logger.exception("unexpected_error_checking_availability", service_id=service_id)
        raise HTTPException(status_code=500, detail="An unexpected error occurred")


@app.post("/book", response_model=BookingResponse)
async def book_appointment(_: ApiKeyDep, request: BookingRequest):
    """
    Book an appointment for a service at a specific time slot.

    This endpoint:
    1. Validates the slot is still available
    2. Finds or creates the customer in Shopmonkey
    3. Finds or creates the vehicle in Shopmonkey
    4. Creates the appointment in Shopmonkey
    """
    logger.info(
        "booking_requested",
        service_id=request.service_id,
        slot_start=request.slot_start,
        slot_end=request.slot_end,
        customer_name=f"{request.customer.firstName} {request.customer.lastName}",
    )

    if not shopmonkey_client or not sheets_client:
        logger.error("clients_not_initialized")
        raise HTTPException(status_code=500, detail="Service temporarily unavailable")

    # Use lock to prevent race conditions during booking
    # NOTE: This only works for single-instance deployments.
    # For multi-instance, use a distributed lock (e.g., Redis).
    async with booking_lock:
        try:
            # Parse slot times
            slot_start_dt = datetime.fromisoformat(request.slot_start.replace("Z", "+00:00"))
            slot_end_dt = datetime.fromisoformat(request.slot_end.replace("Z", "+00:00"))
            date_str = slot_start_dt.strftime("%Y-%m-%d")

            # Get service and qualified techs using shared helper
            service, department, qualified_techs = await get_qualified_techs_for_service(
                request.service_id
            )
            service_name = service.get("name", "Service")
            tech_ids = [t["tech_id"] for t in qualified_techs]

            # Re-check availability (inside lock to prevent race conditions)
            appointments = await shopmonkey_client.get_appointments_for_date(date_str, tech_ids)
            is_available, available_tech_ids = is_slot_available(
                date=slot_start_dt,
                slot_start=slot_start_dt.time(),
                slot_end=slot_end_dt.time(),
                tech_ids=tech_ids,
                appointments=appointments,
            )

            if not is_available:
                logger.warning(
                    "slot_no_longer_available",
                    slot_start=request.slot_start,
                    slot_end=request.slot_end,
                )
                raise HTTPException(
                    status_code=409,
                    detail="The selected time slot is no longer available",
                )

            # Find or create customer
            logger.debug("creating_customer")
            customer = await shopmonkey_client.find_or_create_customer(
                first_name=request.customer.firstName,
                last_name=request.customer.lastName,
                email=request.customer.email,
                phone=request.customer.phone,
            )
            customer_id = customer.get("id")
            if not customer_id:
                logger.error("customer_creation_failed")
                raise HTTPException(status_code=500, detail="Unable to process booking")
            logger.debug("customer_ready", customer_id=customer_id)

            # Find or create vehicle
            logger.debug("creating_vehicle")
            vehicle = await shopmonkey_client.find_or_create_vehicle(
                customer_id=customer_id,
                year=request.vehicle.year,
                make=request.vehicle.make,
                model=request.vehicle.model,
                vin=request.vehicle.vin,
            )
            vehicle_id = vehicle.get("id")
            if not vehicle_id:
                logger.error("vehicle_creation_failed")
                raise HTTPException(status_code=500, detail="Unable to process booking")
            logger.debug("vehicle_ready", vehicle_id=vehicle_id)

            # Create appointment - assign tech by priority + round-robin
            assigned_tech_id = select_tech_by_priority(
                qualified_techs=qualified_techs,
                available_tech_ids=available_tech_ids,
                department=department,
            )
            logger.debug("creating_appointment", technician_id=assigned_tech_id)

            # Generate confirmation number BEFORE creating appointment
            date_part = slot_start_dt.strftime("%Y%m%d")
            unique_part = uuid.uuid4().hex[:6].upper()
            confirmation_number = f"SM-{date_part}-{unique_part}"

            # Get assigned tech name for notes
            assigned_tech_name = None
            for tech in qualified_techs:
                if tech["tech_id"] == assigned_tech_id:
                    assigned_tech_name = tech["tech_name"]
                    break

            # Create enhanced work order notes
            tech_line = f"\nAssign to: {assigned_tech_name}" if assigned_tech_name else ""
            work_order_notes = f"""*** ONLINE BOOKING ***
Confirmation: {confirmation_number}
{tech_line}
Service requested: {service_name}
Booked online via scheduling API."""

            # Format dates as ISO8601 with Central timezone for Shopmonkey API
            # TODO: Make timezone configurable via config.yaml
            start_date_iso = slot_start_dt.strftime("%Y-%m-%dT%H:%M:%S") + ".000-06:00"
            end_date_iso = slot_end_dt.strftime("%Y-%m-%dT%H:%M:%S") + ".000-06:00"

            appointment = await shopmonkey_client.create_appointment(
                customer_id=customer_id,
                vehicle_id=vehicle_id,
                start_date=start_date_iso,
                end_date=end_date_iso,
                title=f"Online Booking: {service_name}",
                notes=work_order_notes,
                technician_id=assigned_tech_id,
            )

            appointment_id = appointment.get("id", "")

            logger.info(
                "booking_successful",
                appointment_id=appointment_id,
                confirmation_number=confirmation_number,
                service_name=service_name,
                technician_id=assigned_tech_id,
            )

            # Send email notification (fire-and-forget, doesn't block response)
            email_client = get_email_client()
            if email_client.enabled:
                booking_details = BookingDetails(
                    confirmation_number=confirmation_number,
                    service_name=service_name,
                    start_time=slot_start_dt,
                    end_time=slot_end_dt,
                    technician_name=assigned_tech_name,
                    customer_first_name=request.customer.firstName,
                    customer_last_name=request.customer.lastName,
                    customer_email=request.customer.email,
                    customer_phone=request.customer.phone,
                    vehicle_year=request.vehicle.year,
                    vehicle_make=request.vehicle.make,
                    vehicle_model=request.vehicle.model,
                )
                asyncio.create_task(
                    email_client.send_booking_notification(booking_details)
                )

            return BookingResponse(
                success=True,
                appointment_id=appointment_id,
                confirmation_number=confirmation_number,
            )

        except HTTPException:
            raise
        except ShopmonkeyAPIError as e:
            logger.error("shopmonkey_api_error_during_booking", error=str(e))
            raise HTTPException(status_code=502, detail="Unable to complete booking")
        except Exception as e:
            logger.exception("unexpected_error_booking", service_id=request.service_id)
            raise HTTPException(status_code=500, detail="An unexpected error occurred")


@app.get("/")
@app.get("/schedule")
async def schedule_page():
    """Serve the scheduling widget page."""
    widget_path = os.path.join(static_dir, "widget.html")
    if not os.path.exists(widget_path):
        raise HTTPException(status_code=404, detail="Scheduling widget not found")
    return FileResponse(widget_path, media_type="text/html")


# Health check endpoints
@app.get("/health", response_model=HealthResponse)
async def health_check():
    """
    Basic liveness probe for Cloud Run / Kubernetes.

    Always returns 200 if the application is running.
    """
    return HealthResponse(status="healthy")


@app.get("/health/live", response_model=HealthResponse)
async def liveness_check():
    """
    Liveness probe - always returns 200 if the application is running.

    Use this for Kubernetes liveness probes.
    """
    return HealthResponse(status="healthy")


@app.get("/health/ready", response_model=ReadinessResponse)
async def readiness_check():
    """
    Readiness probe - checks if dependencies are available.

    Use this for Kubernetes readiness probes. Returns 503 if
    any critical dependency is unavailable.
    """
    shopmonkey_status = "unknown"
    sheets_status = "unknown"
    sheets_cache = None

    if shopmonkey_client:
        try:
            shopmonkey_healthy = await shopmonkey_client.health_check()
            shopmonkey_status = "healthy" if shopmonkey_healthy else "unhealthy"
        except Exception:
            shopmonkey_status = "unhealthy"

    if sheets_client:
        try:
            sheets_healthy = await sheets_client.health_check()
            sheets_status = "healthy" if sheets_healthy else "unhealthy"
            sheets_cache = sheets_client.get_cache_status()
        except Exception:
            sheets_status = "unhealthy"

    overall_status = "healthy" if (shopmonkey_status == "healthy" and sheets_status == "healthy") else "degraded"

    response = ReadinessResponse(
        status=overall_status,
        shopmonkey=shopmonkey_status,
        sheets=sheets_status,
        sheets_cache=sheets_cache,
    )

    if overall_status != "healthy":
        return JSONResponse(
            status_code=503,
            content=response.model_dump(),
        )

    return response


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
