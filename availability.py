"""Business logic for calculating available appointment slots."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import yaml

# Default IANA timezone for the business. Used when config.yaml doesn't set
# `timezone`. zoneinfo handles DST automatically.
DEFAULT_TIMEZONE = "America/Chicago"


def get_timezone(config: dict[str, Any] | None) -> ZoneInfo:
    """Return the ZoneInfo for the business timezone configured in config.yaml.

    Falls back to America/Chicago when no config or no `timezone` key is set.
    """
    if not config:
        return ZoneInfo(DEFAULT_TIMEZONE)
    return ZoneInfo(config.get("timezone") or DEFAULT_TIMEZONE)


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
    with open(config_path) as f:
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


CLOSED = BusinessHours(open_time=None, close_time=None)


def is_closed_by_config(config: dict[str, Any] | None, date: datetime) -> bool:
    """True when `closures.dates` in config.yaml names this date.

    The config half of closure handling. It needs no calendar data, so it is
    applied inside `get_business_hours` - that makes a configured holiday
    close the day for EVERY caller at once, including `get_next_business_day`,
    so a multi-day service rolls over a holiday instead of scheduling work on
    it. The calendar half (`closures.title_patterns`) can only be evaluated
    where a day's appointments are in hand; see `is_closed_by_calendar`.
    """
    if not config:
        return False
    dates = (config.get("closures") or {}).get("dates") or []
    target = date.strftime("%Y-%m-%d")
    return any(str(d).strip() == target for d in dates)


def is_closed_by_calendar(
    appointments: list[dict[str, Any]],
    config: dict[str, Any] | None,
) -> bool:
    """True when the day's calendar carries a shop-wide closure entry.

    A closure entry is one that names NO technician and carries NO `orderId` -
    i.e. nobody's work - whose `name` contains one of `closures.title_patterns`
    (case-insensitive substring). "Labor Day - Closed" qualifies; "Cars &
    Coffee", "HVAC Guy" and "Lunch" do not, and neither does anything assigned
    to a tech or backed by a ticket, because those are somebody's job rather
    than a shutdown.

    A match closes the WHOLE day regardless of the entry's own window. Staff
    stamp these approximately - "Labor Day - Closed" runs 09:00-16:30 against
    09:00-17:30 shop hours - and honoring the literal window would leave a
    17:00 slot bookable on a day the shop is shut.

    `appointments` must already carry `_busyTechIds` (see
    `_fetch_appointments_with_busy_techs`); an entry that names a tech only via
    its order's labors still counts as somebody's work.
    """
    if not config:
        return False
    patterns = [
        str(p).strip().lower() for p in (config.get("closures") or {}).get("title_patterns") or []
    ]
    patterns = [p for p in patterns if p]
    if not patterns:
        return False

    for appt in appointments:
        if appt.get("orderId") or (appt.get("_busyTechIds") or []):
            continue
        name = (appt.get("name") or "").lower()
        if any(p in name for p in patterns):
            return True
    return False


def is_shop_closed(
    date: datetime,
    appointments: list[dict[str, Any]],
    config: dict[str, Any] | None,
) -> bool:
    """True when the shop is closed all day, from either closure source."""
    return is_closed_by_config(config, date) or is_closed_by_calendar(appointments, config)


def get_business_hours(config: dict[str, Any], date: datetime) -> BusinessHours:
    """Get business hours for a specific date.

    Returns closed hours for dates listed in `closures.dates`, so a configured
    holiday is invisible to slot generation and to `get_next_business_day`.
    Calendar-driven closures are handled separately (`is_closed_by_calendar`)
    because they need the day's appointments, which this function never sees.
    """
    if is_closed_by_config(config, date):
        return CLOSED

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


def parse_appointment_times(appointment: dict[str, Any]) -> tuple[datetime, datetime] | None:
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


def get_overlap_info_for_slot(
    slot_start: time,
    slot_end: time,
    date: datetime,
    appointments: list[dict[str, Any]],
    tz: ZoneInfo | None = None,
) -> tuple[set[str], set[str], int]:
    """Inspect every appointment overlapping the slot, returning
    `(unavailable_tech_ids, working_tech_ids, unattributed_overlap_count)`.

    `unavailable_tech_ids` is the union of `_busyTechIds` across ALL
    overlapping appointments, whatever they are. A tech assigned to any
    calendar entry during the slot cannot take a booking - a work order,
    a vacation block, "Mina out", "shop cleaning" all count the same. The
    entry's title and whether a ticket sits behind it are irrelevant; only
    the assignment matters. These techs come out of the qualified pool.

    `working_tech_ids` is the subset assigned to `orderId`-bearing
    appointments - i.e. techs actually turning wrenches on a ticket. Only
    these occupy a service bay, so only these count against a department's
    max-concurrency ceiling. A tech on PTO frees their bay; counting them
    as occupying one would wrongly shrink department concurrency.

    `unattributed_overlap_count` counts overlapping orderId-bearing
    appointments naming no tech at all in either source. We don't know
    who, so each conservatively reduces overall shop capacity by 1. With
    `technicians[]` and the labor walk now unioned upstream this is rare
    (0 of 60 sampled on 2026-08-07), but a ticket with no assignment
    anywhere still shouldn't read as free capacity.

    Entries naming no tech and carrying no `orderId` (shop-wide things like
    "4th of July - Closed" or "Cars & Coffee") are ignored - there's no one
    to block. Shop-wide closures are deliberately out of scope here.
    """
    if tz is None:
        tz = ZoneInfo(DEFAULT_TIMEZONE)

    slot_start_dt = datetime.combine(date.date(), slot_start)
    slot_end_dt = datetime.combine(date.date(), slot_end)

    unavailable_techs: set[str] = set()
    working_techs: set[str] = set()
    unattributed = 0

    for appt in appointments:
        times = parse_appointment_times(appt)
        if times is None:
            continue

        appt_start, appt_end = times

        # Shopmonkey returns appointments in UTC. Convert to the business TZ
        # before stripping tzinfo so the naive comparison stays in the same
        # wall-clock frame as the slot times.
        if appt_start.tzinfo is not None:
            appt_start = appt_start.astimezone(tz).replace(tzinfo=None)
        if appt_end.tzinfo is not None:
            appt_end = appt_end.astimezone(tz).replace(tzinfo=None)

        # Half-open overlap: an appointment ending exactly at slot_start
        # doesn't conflict.
        if not (appt_start < slot_end_dt and appt_end > slot_start_dt):
            continue

        appt_tech_ids = appt.get("_busyTechIds") or []
        has_order = bool(appt.get("orderId"))

        if appt_tech_ids:
            unavailable_techs.update(appt_tech_ids)
            if has_order:
                working_techs.update(appt_tech_ids)
        elif has_order:
            unattributed += 1

    return unavailable_techs, working_techs, unattributed


def count_overlapping_appointments(
    slot_start: time,
    slot_end: time,
    date: datetime,
    appointments: list[dict[str, Any]],
    tz: ZoneInfo | None = None,
) -> int:
    """Count orderId-bearing appointments overlapping the slot.

    Shop-level capacity proxy. Prefer `get_overlap_info_for_slot` when
    you also care which specific techs are busy.
    """
    if tz is None:
        tz = ZoneInfo(DEFAULT_TIMEZONE)

    slot_start_dt = datetime.combine(date.date(), slot_start)
    slot_end_dt = datetime.combine(date.date(), slot_end)

    count = 0
    for appt in appointments:
        if not appt.get("orderId"):
            continue
        times = parse_appointment_times(appt)
        if times is None:
            continue
        appt_start, appt_end = times
        if appt_start.tzinfo is not None:
            appt_start = appt_start.astimezone(tz).replace(tzinfo=None)
        if appt_end.tzinfo is not None:
            appt_end = appt_end.astimezone(tz).replace(tzinfo=None)
        if appt_start < slot_end_dt and appt_end > slot_start_dt:
            count += 1
    return count


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

    # A start at or after closing leaves no working minutes on day 1. Falling
    # through would make minutes_until_close negative, so the caller would build
    # a first segment whose end precedes its start and then bill the "remaining"
    # duration to a spurious continuation day - corrupt data written into
    # Shopmonkey. /availability never offers such a start, but /book takes
    # slot_start from the client, so a stale widget or hand-crafted request
    # reaches here. Refuse instead (the caller turns this into a 409).
    if minutes_until_close <= 0:
        return None

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


def cap_by_concurrency(
    capacity: int,
    occupied_count: int,
    max_concurrency: int | None,
) -> int:
    """Clamp tech-based capacity by a department's max service concurrency.

    The occupancy ceiling models a physical resource (bays / equipment): a
    department may run at most `max_concurrency` services at once, so the
    remaining concurrency is `max_concurrency - occupied_count`. We never
    offer more than that, nor more than the free-tech `capacity`.

    `occupied_count` is how many qualified techs are working a ticket that
    overlaps the slot - passed in rather than inferred from the free-tech
    count, because "not free" and "occupying a bay" are different things: a
    tech on PTO is unavailable but holds no bay, and inferring occupancy
    from absence would wrongly shrink the department's concurrency.

    Unattributed overlaps (orderId-bearing appointments naming no tech) are
    intentionally NOT counted toward department occupancy: they already
    reduce the free-tech `capacity`, and we can't attribute them to a
    specific department. `max_concurrency is None` means no cap (the
    original tech-only behavior).
    """
    if max_concurrency is None:
        return capacity
    return max(min(capacity, max_concurrency - occupied_count), 0)


def slot_capacity(
    slot_start: time,
    slot_end: time,
    date: datetime,
    appointments: list[dict[str, Any]],
    tech_ids: list[str],
    tz: ZoneInfo | None = None,
    max_concurrency: int | None = None,
) -> tuple[int, list[str]]:
    """Compute `(remaining_capacity, free_tech_ids)` for a slot.

    Drops from the qualified pool every tech assigned to an overlapping
    calendar entry - work order, vacation, lunch, anything (see
    `get_overlap_info_for_slot`) - then subtracts the unattributed overlap
    count (orderId-bearing appointments naming no tech) as a conservative
    shop-level reduction.

    `free_tech_ids` are techs we KNOW hold no overlapping assignment. When
    unattributed > 0, the caller still treats overall capacity as
    `len(free_tech_ids) - unattributed`, because we can't tell which of the
    "free" techs might actually be the unattributed-busy one.

    `max_concurrency`, when set, further clamps the result to the department's
    maximum simultaneous services (see `cap_by_concurrency`), counting only
    qualified techs on a ticket - time-off doesn't consume a bay. The returned
    `free_tech_ids` list is unchanged - the cap only limits how many of those
    free techs may be offered/booked, and assignment still picks from the list.
    """
    unavailable, working, unattributed = get_overlap_info_for_slot(
        slot_start, slot_end, date, appointments, tz=tz
    )
    free = [t for t in tech_ids if t not in unavailable]
    capacity = max(len(free) - unattributed, 0)
    occupied = len([t for t in tech_ids if t in working])
    capacity = cap_by_concurrency(capacity, occupied, max_concurrency)
    return capacity, free


def count_multiday_overlap_capacity(
    days_needed: list[tuple[datetime, int]],
    first_day_appointments: list[dict[str, Any]],
    first_day_start_time: time,
    first_day_close_time: time,
    future_appointments: dict[str, list[dict[str, Any]]],
    config: dict[str, Any],
    tech_ids: list[str],
    max_concurrency: int | None = None,
) -> tuple[int, list[str]]:
    """Compute remaining capacity and free techs for a multi-day slot.

    Returns `(min_capacity, free_tech_ids)` where free_tech_ids is the
    INTERSECTION of free techs across every required day (a tech has to
    be free on all spanned days to take a multi-day booking). The
    bottleneck day governs the capacity number.

    `max_concurrency`, when set, clamps each day's capacity to the
    department's max simultaneous services; since the bottleneck day already
    governs `min_capacity`, the day with the least remaining concurrency wins.
    """
    if not days_needed:
        return 0, []

    tz = get_timezone(config)

    # First day: cap from start_time to close
    first_date, _ = days_needed[0]
    first_capacity, first_free = slot_capacity(
        first_day_start_time,
        first_day_close_time,
        first_date,
        first_day_appointments,
        tech_ids,
        tz=tz,
        max_concurrency=max_concurrency,
    )
    min_capacity = first_capacity
    free_set = set(first_free)

    # Subsequent days: from open until minutes_needed
    for date, minutes_needed in days_needed[1:]:
        day_hours = get_business_hours(config, date)
        if not day_hours.is_open:
            return 0, []

        needed_end = (
            datetime.combine(date.date(), day_hours.open_time) + timedelta(minutes=minutes_needed)
        ).time()

        date_str = date.strftime("%Y-%m-%d")
        day_appointments = future_appointments.get(date_str, [])

        # A continuation day carrying a shop-wide closure entry has no capacity
        # at all, so the whole multi-day slot is off. (Config-listed holidays
        # never reach here - get_business_hours already reports them closed,
        # which is caught by the is_open guard above.)
        if is_closed_by_calendar(day_appointments, config):
            return 0, []

        cap, free = slot_capacity(
            day_hours.open_time,
            needed_end,
            date,
            day_appointments,
            tech_ids,
            tz=tz,
            max_concurrency=max_concurrency,
        )
        if cap < min_capacity:
            min_capacity = cap
        free_set &= set(free)

    # Preserve qualified order in the returned free list
    free_in_order = [t for t in tech_ids if t in free_set]
    # A multi-day booking needs ONE tech free on every spanned day, so capacity
    # can never exceed the size of the cross-day intersection. Without this the
    # two halves of the return value disagree: with techs {A} free on day 1 and
    # {B} on day 2, the bottleneck day reports capacity 1 while the intersection
    # is empty, so /availability advertises the slot and /book confirms it with
    # no technician assigned.
    return max(min(min_capacity, len(free_in_order)), 0), free_in_order


def collect_multiday_future_dates(
    date: datetime,
    duration_minutes: int,
    config: dict[str, Any],
) -> list[str]:
    """Return the future date strings ("YYYY-MM-DD") that any slot on `date`
    could roll over into, given the service duration.

    A slot is multi-day when the duration exceeds the minutes between its
    start and close, so late starts need their continuation days' schedules
    to judge capacity. This is the union across every generated slot start,
    so callers can fetch exactly the days the multi-day capacity check will
    look at - no more, no fewer. Empty list when no slot rolls over.
    """
    business_hours = get_business_hours(config, date)
    if not business_hours.is_open:
        return []

    slot_interval = config.get("slot_interval_minutes", 60)
    future_dates: set[str] = set()
    for slot_start in generate_slot_start_times(business_hours, slot_interval):
        days_needed = calculate_days_needed(duration_minutes, date, slot_start, config)
        if not days_needed:
            continue
        for day, _ in days_needed[1:]:
            future_dates.add(day.strftime("%Y-%m-%d"))

    return sorted(future_dates)


def check_slot_availability_for_duration(
    date: datetime,
    slot_start: time,
    duration_minutes: int,
    tech_ids: list[str],
    appointments: list[dict[str, Any]],
    future_appointments: dict[str, list[dict[str, Any]]],
    config: dict[str, Any],
    max_concurrency: int | None = None,
) -> tuple[bool, list[str], list[tuple[datetime, int]]]:
    """Re-check a slot for /book, deriving the real span from the service
    duration instead of trusting the client-submitted slot_end.

    Single-day slots check `slot_start → slot_start + duration`. Multi-day
    slots check the first day through close plus the needed window on each
    continuation day (same logic the /availability endpoint advertises
    slots with).

    Returns `(is_available, free_tech_ids, days_needed)`. `days_needed` is
    the `calculate_days_needed` segmentation so the caller can create one
    appointment per day; empty list when the slot can't be completed at all
    (closed day, or service overflows the 7-business-day lookahead).
    """
    days_needed = calculate_days_needed(duration_minutes, date, slot_start, config)
    if not days_needed:
        return (False, [], [])

    # Mirror of the /availability closure filter, inside the booking lock. A
    # stale widget still holding yesterday's slot list must not be able to book
    # a day that has since been marked closed.
    if is_closed_by_calendar(appointments, config):
        return (False, [], days_needed)

    tz = get_timezone(config)

    if len(days_needed) == 1:
        slot_end = (
            datetime.combine(date.date(), slot_start) + timedelta(minutes=duration_minutes)
        ).time()
        capacity, free_techs = slot_capacity(
            slot_start,
            slot_end,
            date,
            appointments,
            tech_ids,
            tz=tz,
            max_concurrency=max_concurrency,
        )
    else:
        business_hours = get_business_hours(config, date)
        capacity, free_techs = count_multiday_overlap_capacity(
            days_needed=days_needed,
            first_day_appointments=appointments,
            first_day_start_time=slot_start,
            first_day_close_time=business_hours.close_time,
            future_appointments=future_appointments,
            config=config,
            tech_ids=tech_ids,
            max_concurrency=max_concurrency,
        )

    if capacity <= 0:
        return (False, [], days_needed)
    return (True, free_techs, days_needed)


def build_appointment_segments(
    days_needed: list[tuple[datetime, int]],
    slot_start: time,
    config: dict[str, Any],
) -> list[tuple[datetime, datetime]]:
    """Convert a `calculate_days_needed` segmentation into concrete
    (start_dt, end_dt) appointment windows, one per business day.

    Day 1 runs from the requested start for its allotted minutes (through
    close for multi-day bookings); continuation days run from open until
    their remaining minutes are used up.
    """
    segments: list[tuple[datetime, datetime]] = []
    for i, (day, minutes_needed) in enumerate(days_needed):
        if i == 0:
            seg_start = datetime.combine(day.date(), slot_start)
        else:
            day_hours = get_business_hours(config, day)
            seg_start = datetime.combine(day.date(), day_hours.open_time)
        segments.append((seg_start, seg_start + timedelta(minutes=minutes_needed)))
    return segments


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
    max_concurrency: int | None = None,
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
        max_concurrency: Department's maximum simultaneous services (an
            occupancy ceiling on top of free-tech capacity); None means no cap.

    Returns:
        List of TimeSlot objects with availability info
    """
    business_hours = get_business_hours(config, date)

    if not business_hours.is_open:
        return []

    # A shop-wide closure entry on the calendar ("Labor Day - Closed") takes the
    # whole day off the board. Checked here rather than in get_business_hours
    # because only this layer has the day's appointments.
    if is_closed_by_calendar(appointments, config):
        return []

    if slot_duration_minutes is None:
        slot_duration_minutes = config.get("default_slot_duration_minutes", 60)

    if future_appointments is None:
        future_appointments = {}

    tz = get_timezone(config)

    # Generate slot start times (hourly intervals)
    slot_interval = config.get("slot_interval_minutes", 60)
    slot_starts = generate_slot_start_times(business_hours, slot_interval)

    available_slots = []

    for slot_start in slot_starts:
        # Calculate days needed for this service starting at slot_start
        days_needed = calculate_days_needed(slot_duration_minutes, date, slot_start, config)

        if days_needed is None:
            # Can't complete service within reasonable timeframe
            continue

        is_multiday = len(days_needed) > 1

        if is_multiday:
            capacity, free_techs = count_multiday_overlap_capacity(
                days_needed=days_needed,
                first_day_appointments=appointments,
                first_day_start_time=slot_start,
                first_day_close_time=business_hours.close_time,
                future_appointments=future_appointments,
                config=config,
                tech_ids=tech_ids,
                max_concurrency=max_concurrency,
            )
            slot_end = business_hours.close_time
        else:
            slot_end = (
                datetime.combine(date.date(), slot_start) + timedelta(minutes=slot_duration_minutes)
            ).time()
            capacity, free_techs = slot_capacity(
                slot_start,
                slot_end,
                date,
                appointments,
                tech_ids,
                tz=tz,
                max_concurrency=max_concurrency,
            )

        if capacity > 0:
            # free_techs are the qualified techs whose labor.technicianId
            # does NOT appear on any overlapping order. priority +
            # round-robin picks one of them for assignment.
            available_slots.append(
                TimeSlot(
                    start=slot_start,
                    end=slot_end,
                    available_techs=capacity,
                    available_tech_ids=free_techs,
                )
            )

    return available_slots


def drop_elapsed_slots(
    slots: list[TimeSlot],
    date: datetime,
    now: datetime,
) -> list[TimeSlot]:
    """Remove slots that have already started relative to `now`.

    `now` and `date` are naive wall-clock datetimes in the business timezone
    (the same convention used elsewhere in this module). A slot survives only
    if its start is strictly in the future:

    - past date  -> every slot's start is <= now, so all are dropped
    - today      -> only slots that haven't started yet remain
    - future date-> all starts are > now, so the list is unchanged

    This prevents offering e.g. a 9:00 AM slot once it's already noon.
    """
    return [s for s in slots if datetime.combine(date.date(), s.start) > now]


def is_slot_available(
    date: datetime,
    slot_start: time,
    slot_end: time,
    tech_ids: list[str],
    appointments: list[dict[str, Any]],
    config: dict[str, Any] | None = None,
    max_concurrency: int | None = None,
) -> tuple[bool, list[str]]:
    """Re-check that a specific slot still has capacity for a qualified tech.

    Used by /book inside the booking lock to catch races between the
    availability check and the actual create. Returns the same
    `(is_available, eligible_tech_ids)` shape as before; the eligible
    list now reflects techs known free (not busy on a labor for an
    overlapping order). See `get_overlap_info_for_slot` for the labor-tech
    chain walk.
    """
    tz = get_timezone(config)
    capacity, free_techs = slot_capacity(
        slot_start,
        slot_end,
        date,
        appointments,
        tech_ids,
        tz=tz,
        max_concurrency=max_concurrency,
    )
    if capacity <= 0:
        return (False, [])
    return (True, free_techs)


def get_buffer_minutes(
    service: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> int:
    """
    Get buffer time for a service from labels or config.

    Buffer time is extra scheduling time (e.g., cure time for bedliners)
    that doesn't affect labor hours/pricing.

    Priority:
    1. Service-specific "buffer:X" label in Shopmonkey (highest priority)
    2. Config-based buffer by department label (service_buffers in config.yaml)
    3. 0 if no buffer configured

    Args:
        service: Shopmonkey canned service dict
        config: Configuration dict with optional service_buffers section

    Returns:
        Buffer time in minutes
    """
    labels = service.get("labels", [])

    # Priority 1: Check for explicit buffer:X label on the service
    for label in labels:
        name = label.get("name", "")
        if name.lower().startswith("buffer:"):
            try:
                return int(name.split(":")[1])
            except (ValueError, IndexError):
                pass

    # Priority 2: Check config for department-based buffer
    if config and labels:
        service_buffers = config.get("service_buffers", {})
        if service_buffers:
            # Check each label against configured buffers
            for label in labels:
                label_name = label.get("name", "")
                if label_name in service_buffers:
                    try:
                        return int(service_buffers[label_name])
                    except (ValueError, TypeError):
                        pass

    return 0


# Keep old function name as alias for backward compatibility
def get_buffer_minutes_from_labels(service: dict[str, Any]) -> int:
    """Deprecated: Use get_buffer_minutes instead."""
    return get_buffer_minutes(service, config=None)


def get_service_duration_minutes(service: dict[str, Any], default_duration: int = 60) -> int:
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
            # round(), not int(): binary floating point makes some exact
            # quarter-hours land just under their true product (2.05 * 60 is
            # 122.99999999999999), and truncating there under-books the service
            # by a minute on every appointment of that duration.
            return round(total_hours * 60)

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
