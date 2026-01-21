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


def check_slot_conflicts(
    slot_start: time,
    slot_end: time,
    date: datetime,
    appointments: list[dict[str, Any]],
    tech_id: str,
) -> bool:
    """
    Check if a tech has a conflicting appointment during a time slot.

    Returns True if there's a conflict, False if the slot is free.
    """
    slot_start_dt = datetime.combine(date.date(), slot_start)
    slot_end_dt = datetime.combine(date.date(), slot_end)

    for appt in appointments:
        # Check if this appointment belongs to this tech
        appt_tech_id = appt.get("technicianId") or appt.get("userId")
        if appt_tech_id != tech_id:
            continue

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

    # Generate slot start times (hourly intervals)
    slot_interval = config.get("slot_interval_minutes", 60)
    slot_starts = generate_slot_start_times(business_hours, slot_interval)

    available_slots = []
    close_dt = datetime.combine(date.date(), business_hours.close_time)

    for slot_start in slot_starts:
        start_dt = datetime.combine(date.date(), slot_start)
        end_dt = start_dt + timedelta(minutes=slot_duration_minutes)

        # Calculate if service extends past closing
        minutes_until_close = int((close_dt - start_dt).total_seconds() / 60)
        extends_past_close = slot_duration_minutes > minutes_until_close

        if extends_past_close:
            # Service requires overnight - calculate days needed
            remaining_minutes = slot_duration_minutes - minutes_until_close
            days_needed = 1

            # Check subsequent days for remaining work
            check_date = date
            overflow = remaining_minutes
            continuation_feasible = True

            while overflow > 0 and continuation_feasible:
                next_day = get_next_business_day(check_date, config)
                if next_day is None:
                    continuation_feasible = False
                    break

                next_hours = get_business_hours(config, next_day)
                next_open = datetime.combine(next_day.date(), next_hours.open_time)
                next_close = datetime.combine(next_day.date(), next_hours.close_time)
                day_minutes = int((next_close - next_open).total_seconds() / 60)

                days_needed += 1
                overflow -= day_minutes
                check_date = next_day

            if not continuation_feasible:
                # Can't complete service within reasonable timeframe
                continue

            # Check tech availability for the starting slot AND continuation days
            available_tech_ids = []

            for tech_id in tech_ids:
                # Check day 1 availability (from slot_start to close)
                has_conflict = check_slot_conflicts(
                    slot_start, business_hours.close_time, date, appointments, tech_id
                )
                if has_conflict:
                    continue

                # Check availability at start of subsequent days
                tech_available_all_days = True
                check_date = date
                overflow = remaining_minutes

                while overflow > 0:
                    next_day = get_next_business_day(check_date, config)
                    if next_day is None:
                        tech_available_all_days = False
                        break

                    next_hours = get_business_hours(config, next_day)
                    next_open = next_hours.open_time
                    next_close = next_hours.close_time
                    day_minutes = int((datetime.combine(next_day.date(), next_close) -
                                      datetime.combine(next_day.date(), next_open)).total_seconds() / 60)

                    # Determine how long tech is needed on this day
                    needed_minutes = min(overflow, day_minutes)
                    needed_end = (datetime.combine(next_day.date(), next_open) +
                                 timedelta(minutes=needed_minutes)).time()

                    # Get appointments for next day
                    next_day_str = next_day.strftime("%Y-%m-%d")
                    next_day_appointments = future_appointments.get(next_day_str, [])

                    has_conflict = check_slot_conflicts(
                        next_open, needed_end, next_day, next_day_appointments, tech_id
                    )
                    if has_conflict:
                        tech_available_all_days = False
                        break

                    overflow -= day_minutes
                    check_date = next_day

                if tech_available_all_days:
                    available_tech_ids.append(tech_id)

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
            slot_end = end_dt.time()
            available_tech_ids = []

            for tech_id in tech_ids:
                has_conflict = check_slot_conflicts(
                    slot_start, slot_end, date, appointments, tech_id
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
