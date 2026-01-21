"""Business logic for calculating available appointment slots."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any

import yaml


@dataclass
class TimeSlot:
    """Represents a bookable time slot."""

    start: time
    end: time
    available_techs: int
    available_tech_ids: list[str]


@dataclass
class BusinessHours:
    """Business hours for a day."""

    open_time: time | None
    close_time: time | None

    @property
    def is_open(self) -> bool:
        return self.open_time is not None and self.close_time is not None


def load_config(config_path: str = "config.yaml") -> dict[str, Any]:
    """Load configuration from YAML file."""
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def validate_config(config: dict[str, Any]) -> None:
    """
    Validate configuration dictionary has required keys and valid formats.

    Raises:
        ValueError: If configuration is invalid with descriptive message
    """
    if not config:
        raise ValueError("Configuration is empty or None")

    # Check required keys
    if "business_hours" not in config:
        raise ValueError("Configuration missing required key: 'business_hours'")

    if "default_slot_duration_minutes" not in config:
        raise ValueError("Configuration missing required key: 'default_slot_duration_minutes'")

    business_hours = config["business_hours"]
    if not isinstance(business_hours, dict):
        raise ValueError("'business_hours' must be a dictionary")

    # Validate business hours format for each configured day
    valid_days = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
    for day, hours in business_hours.items():
        if day.lower() not in valid_days:
            raise ValueError(f"Invalid day name in business_hours: '{day}'")

        # Allow null/None for closed days
        if hours is None:
            continue

        if not isinstance(hours, dict):
            raise ValueError(f"Business hours for '{day}' must be a dictionary or null")

        # Validate open/close times if present
        if hours.get("open"):
            try:
                datetime.strptime(hours["open"], "%H:%M")
            except ValueError:
                raise ValueError(
                    f"Invalid open time format for '{day}': '{hours['open']}'. "
                    "Expected HH:MM format (e.g., '09:00')"
                )

        if hours.get("close"):
            try:
                datetime.strptime(hours["close"], "%H:%M")
            except ValueError:
                raise ValueError(
                    f"Invalid close time format for '{day}': '{hours['close']}'. "
                    "Expected HH:MM format (e.g., '17:00')"
                )

    # Validate slot duration
    duration = config["default_slot_duration_minutes"]
    if not isinstance(duration, (int, float)) or duration <= 0:
        raise ValueError(
            f"'default_slot_duration_minutes' must be a positive number, got: {duration}"
        )


def get_business_hours(config: dict[str, Any], date: datetime) -> BusinessHours:
    """Get business hours for a specific date."""
    day_name = date.strftime("%A").lower()
    day_config = config.get("business_hours", {}).get(day_name)

    if day_config is None:
        return BusinessHours(open_time=None, close_time=None)

    open_str = day_config.get("open")
    close_str = day_config.get("close")

    if not open_str or not close_str:
        return BusinessHours(open_time=None, close_time=None)

    open_time = datetime.strptime(open_str, "%H:%M").time()
    close_time = datetime.strptime(close_str, "%H:%M").time()

    return BusinessHours(open_time=open_time, close_time=close_time)


def generate_time_slots(
    business_hours: BusinessHours,
    slot_duration_minutes: int,
) -> list[tuple[time, time]]:
    """
    Generate all possible time slots for a day based on business hours.

    Returns list of (start_time, end_time) tuples.
    """
    if not business_hours.is_open:
        return []

    slots = []
    current = datetime.combine(datetime.today(), business_hours.open_time)
    close_dt = datetime.combine(datetime.today(), business_hours.close_time)
    slot_delta = timedelta(minutes=slot_duration_minutes)

    while current + slot_delta <= close_dt:
        slot_end = current + slot_delta
        slots.append((current.time(), slot_end.time()))
        current = slot_end

    return slots


def parse_appointment_times(
    appointment: dict[str, Any]
) -> tuple[datetime, datetime] | None:
    """Parse start and end times from a Shopmonkey appointment."""
    start_str = appointment.get("startDate")
    end_str = appointment.get("endDate")

    if not start_str or not end_str:
        return None

    # Handle ISO format with Z suffix
    start_str = start_str.replace("Z", "+00:00")
    end_str = end_str.replace("Z", "+00:00")

    try:
        start = datetime.fromisoformat(start_str)
        end = datetime.fromisoformat(end_str)
        return (start, end)
    except ValueError:
        return None


def index_appointments_by_tech(
    appointments: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    """
    Index appointments by technician ID for O(1) lookup.

    Args:
        appointments: List of appointment dictionaries

    Returns:
        Dict mapping tech_id to list of their appointments
    """
    indexed: dict[str, list[dict[str, Any]]] = {}
    for appt in appointments:
        tech_id = appt.get("technicianId") or appt.get("userId")
        if tech_id:
            if tech_id not in indexed:
                indexed[tech_id] = []
            indexed[tech_id].append(appt)
    return indexed


def check_slot_conflicts(
    slot_start: time,
    slot_end: time,
    date: datetime,
    appointments: list[dict[str, Any]],
    tech_id: str,
    indexed_appointments: dict[str, list[dict[str, Any]]] | None = None,
) -> bool:
    """
    Check if a tech has a conflicting appointment during a time slot.

    Args:
        slot_start: Start time of the slot
        slot_end: End time of the slot
        date: Date to check
        appointments: List of appointments (used if indexed_appointments not provided)
        tech_id: Technician ID to check
        indexed_appointments: Optional pre-indexed appointments by tech_id for O(1) lookup

    Returns:
        True if there's a conflict, False if the slot is free.
    """
    slot_start_dt = datetime.combine(date.date(), slot_start)
    slot_end_dt = datetime.combine(date.date(), slot_end)

    # Use indexed appointments if provided, otherwise filter from full list
    if indexed_appointments is not None:
        tech_appointments = indexed_appointments.get(tech_id, [])
    else:
        tech_appointments = [
            appt for appt in appointments
            if (appt.get("technicianId") or appt.get("userId")) == tech_id
        ]

    for appt in tech_appointments:
        times = parse_appointment_times(appt)
        if times is None:
            continue

        appt_start, appt_end = times

        # Make naive for comparison if needed
        if appt_start.tzinfo is not None:
            appt_start = appt_start.replace(tzinfo=None)
        if appt_end.tzinfo is not None:
            appt_end = appt_end.replace(tzinfo=None)

        # Check for overlap
        if appt_start < slot_end_dt and appt_end > slot_start_dt:
            return True  # Conflict found

    return False  # No conflict


def get_next_business_day(date: datetime, config: dict[str, Any]) -> datetime | None:
    """Find the next business day after the given date."""
    next_date = date + timedelta(days=1)
    # Check up to 7 days ahead to handle weekends/holidays
    for _ in range(7):
        hours = get_business_hours(config, next_date)
        if hours.is_open:
            return next_date
        next_date = next_date + timedelta(days=1)
    return None


def calculate_days_needed(
    duration_minutes: int,
    start_date: datetime,
    start_time: time,
    config: dict[str, Any],
) -> list[tuple[datetime, int]] | None:
    """
    Calculate the business days needed for a multi-day service.

    For services that extend past closing time, this function determines
    which days are needed and how many minutes are required on each day.

    Args:
        duration_minutes: Total service duration in minutes
        start_date: The starting date
        start_time: The starting time on the first day
        config: Configuration with business hours

    Returns:
        List of (date, minutes_needed) tuples, or None if service cannot
        be completed within reasonable timeframe (7 business days max)
    """
    business_hours = get_business_hours(config, start_date)
    if not business_hours.is_open:
        return None

    start_dt = datetime.combine(start_date.date(), start_time)
    close_dt = datetime.combine(start_date.date(), business_hours.close_time)
    minutes_until_close = int((close_dt - start_dt).total_seconds() / 60)

    # If service fits in first day, return single day
    if duration_minutes <= minutes_until_close:
        return [(start_date, duration_minutes)]

    days_needed: list[tuple[datetime, int]] = []
    # First day: work from start_time until close
    days_needed.append((start_date, minutes_until_close))
    remaining_minutes = duration_minutes - minutes_until_close

    check_date = start_date
    while remaining_minutes > 0:
        next_day = get_next_business_day(check_date, config)
        if next_day is None:
            return None  # Cannot complete within reasonable timeframe

        next_hours = get_business_hours(config, next_day)
        next_open = datetime.combine(next_day.date(), next_hours.open_time)
        next_close = datetime.combine(next_day.date(), next_hours.close_time)
        day_minutes = int((next_close - next_open).total_seconds() / 60)

        minutes_on_this_day = min(remaining_minutes, day_minutes)
        days_needed.append((next_day, minutes_on_this_day))

        remaining_minutes -= day_minutes
        check_date = next_day

    return days_needed


def check_tech_multiday_availability(
    tech_id: str,
    days_needed: list[tuple[datetime, int]],
    first_day_appointments: list[dict[str, Any]],
    first_day_start_time: time,
    first_day_close_time: time,
    future_appointments: dict[str, list[dict[str, Any]]],
    config: dict[str, Any],
    indexed_first_day: dict[str, list[dict[str, Any]]] | None = None,
) -> bool:
    """
    Check if a technician is available for all days of a multi-day service.

    Args:
        tech_id: Technician ID to check
        days_needed: List of (date, minutes_needed) from calculate_days_needed
        first_day_appointments: Appointments for the first day
        first_day_start_time: Service start time on first day
        first_day_close_time: Business close time on first day
        future_appointments: Dict mapping date strings to appointment lists
        config: Configuration with business hours
        indexed_first_day: Optional pre-indexed first day appointments

    Returns:
        True if tech is available for all required days, False otherwise
    """
    if not days_needed:
        return False

    # Check first day availability (from start_time to close)
    first_date, _ = days_needed[0]
    has_conflict = check_slot_conflicts(
        first_day_start_time,
        first_day_close_time,
        first_date,
        first_day_appointments,
        tech_id,
        indexed_first_day,
    )
    if has_conflict:
        return False

    # Check subsequent days
    for date, minutes_needed in days_needed[1:]:
        day_hours = get_business_hours(config, date)
        if not day_hours.is_open:
            return False

        # Tech needs to be free from open until minutes_needed
        needed_end = (
            datetime.combine(date.date(), day_hours.open_time)
            + timedelta(minutes=minutes_needed)
        ).time()

        date_str = date.strftime("%Y-%m-%d")
        day_appointments = future_appointments.get(date_str, [])

        # Index future day appointments for this check
        indexed_future = index_appointments_by_tech(day_appointments)

        has_conflict = check_slot_conflicts(
            day_hours.open_time,
            needed_end,
            date,
            day_appointments,
            tech_id,
            indexed_future,
        )
        if has_conflict:
            return False

    return True


def generate_slot_start_times(
    business_hours: BusinessHours,
    slot_interval_minutes: int = 60,
) -> list[time]:
    """
    Generate possible slot start times throughout the business day.

    Uses a fixed interval (default 60 min) for start times, regardless of service duration.
    """
    if not business_hours.is_open:
        return []

    starts = []
    current = datetime.combine(datetime.today(), business_hours.open_time)
    close_dt = datetime.combine(datetime.today(), business_hours.close_time)
    interval = timedelta(minutes=slot_interval_minutes)

    while current < close_dt:
        starts.append(current.time())
        current = current + interval

    return starts


def calculate_available_slots(
    date: datetime,
    tech_ids: list[str],
    appointments: list[dict[str, Any]],
    config: dict[str, Any],
    slot_duration_minutes: int | None = None,
    future_appointments: dict[str, list[dict[str, Any]]] | None = None,
) -> list[TimeSlot]:
    """
    Calculate available time slots for a given date.

    Handles multi-day services by checking tech availability on subsequent
    business days when a service extends past closing time.

    Args:
        date: The date to check availability for
        tech_ids: List of qualified technician IDs
        appointments: List of existing appointments for the date
        config: Configuration dict with business hours
        slot_duration_minutes: Duration of each slot (uses config default if not provided)
        future_appointments: Dict mapping date strings to appointments for those dates
                           (used for checking multi-day availability)

    Returns:
        List of TimeSlot objects with availability info
    """
    business_hours = get_business_hours(config, date)

    if not business_hours.is_open:
        return []

    if slot_duration_minutes is None:
        slot_duration_minutes = config.get("default_slot_duration_minutes", 60)

    if future_appointments is None:
        future_appointments = {}

    # Index appointments once for O(1) lookup per tech
    indexed_appointments = index_appointments_by_tech(appointments)

    # Generate slot start times (hourly intervals)
    slot_interval = config.get("slot_interval_minutes", 60)
    slot_starts = generate_slot_start_times(business_hours, slot_interval)

    available_slots = []

    for slot_start in slot_starts:
        # Calculate days needed for this service starting at slot_start
        days_needed = calculate_days_needed(
            slot_duration_minutes, date, slot_start, config
        )

        if days_needed is None:
            # Can't complete service within reasonable timeframe
            continue

        is_multiday = len(days_needed) > 1

        if is_multiday:
            available_tech_ids = _calculate_multiday_slot_availability(
                tech_ids=tech_ids,
                days_needed=days_needed,
                first_day_appointments=appointments,
                first_day_start_time=slot_start,
                business_hours=business_hours,
                future_appointments=future_appointments,
                config=config,
                indexed_appointments=indexed_appointments,
            )

            if available_tech_ids:
                # For multi-day services, end time is close of first day
                available_slots.append(
                    TimeSlot(
                        start=slot_start,
                        end=business_hours.close_time,
                        available_techs=len(available_tech_ids),
                        available_tech_ids=available_tech_ids,
                    )
                )
        else:
            # Service fits within business hours
            slot_end = (
                datetime.combine(date.date(), slot_start)
                + timedelta(minutes=slot_duration_minutes)
            ).time()

            available_tech_ids = []
            for tech_id in tech_ids:
                has_conflict = check_slot_conflicts(
                    slot_start, slot_end, date, appointments, tech_id, indexed_appointments
                )
                if not has_conflict:
                    available_tech_ids.append(tech_id)

            if available_tech_ids:
                available_slots.append(
                    TimeSlot(
                        start=slot_start,
                        end=slot_end,
                        available_techs=len(available_tech_ids),
                        available_tech_ids=available_tech_ids,
                    )
                )

    return available_slots


def _calculate_multiday_slot_availability(
    tech_ids: list[str],
    days_needed: list[tuple[datetime, int]],
    first_day_appointments: list[dict[str, Any]],
    first_day_start_time: time,
    business_hours: BusinessHours,
    future_appointments: dict[str, list[dict[str, Any]]],
    config: dict[str, Any],
    indexed_appointments: dict[str, list[dict[str, Any]]],
) -> list[str]:
    """
    Calculate which techs are available for a multi-day service slot.

    This is an internal helper for calculate_available_slots that handles
    the complexity of checking tech availability across multiple days.

    Args:
        tech_ids: List of technician IDs to check
        days_needed: Days and minutes required from calculate_days_needed
        first_day_appointments: Appointments for the first day
        first_day_start_time: When the service would start on day 1
        business_hours: Business hours for the first day
        future_appointments: Dict of date string -> appointments for future days
        config: Configuration with business hours
        indexed_appointments: Pre-indexed first day appointments by tech ID

    Returns:
        List of tech IDs available for all required days
    """
    available_tech_ids = []

    for tech_id in tech_ids:
        is_available = check_tech_multiday_availability(
            tech_id=tech_id,
            days_needed=days_needed,
            first_day_appointments=first_day_appointments,
            first_day_start_time=first_day_start_time,
            first_day_close_time=business_hours.close_time,
            future_appointments=future_appointments,
            config=config,
            indexed_first_day=indexed_appointments,
        )
        if is_available:
            available_tech_ids.append(tech_id)

    return available_tech_ids


def is_slot_available(
    date: datetime,
    slot_start: time,
    slot_end: time,
    tech_ids: list[str],
    appointments: list[dict[str, Any]],
) -> tuple[bool, list[str]]:
    """
    Check if a specific slot is still available.

    Returns:
        Tuple of (is_available, list of available tech IDs)
    """
    available_tech_ids = []

    for tech_id in tech_ids:
        has_conflict = check_slot_conflicts(
            slot_start, slot_end, date, appointments, tech_id
        )
        if not has_conflict:
            available_tech_ids.append(tech_id)

    return (len(available_tech_ids) > 0, available_tech_ids)


def get_service_duration_minutes(
    service: dict[str, Any], default_duration: int = 60
) -> int:
    """
    Extract service duration from Shopmonkey canned service.

    Shopmonkey stores labor time in the labors array with an 'hours' field.
    We sum all labor hours and convert to minutes.
    """
    # First, try to get duration from labors array (primary source)
    labors = service.get("labors", [])
    if labors:
        total_hours = 0.0
        for labor in labors:
            hours = labor.get("hours") or 0
            try:
                total_hours += float(hours)
            except (ValueError, TypeError):
                pass
        if total_hours > 0:
            return int(total_hours * 60)

    # Fallback: try common field names for duration
    duration = (
        service.get("estimatedDuration")
        or service.get("duration")
        or service.get("estimatedMinutes")
    )

    if duration is not None:
        try:
            return int(duration)
        except (ValueError, TypeError):
            pass

    return default_duration
