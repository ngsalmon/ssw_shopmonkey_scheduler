"""Unit tests for availability calculation logic."""

import pytest
import sys
from pathlib import Path
from datetime import datetime, time, timedelta

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from availability import (
    BusinessHours,
    TimeSlot,
    get_business_hours,
    generate_time_slots,
    parse_appointment_times,
    check_slot_conflicts,
    calculate_available_slots,
    is_slot_available,
    get_service_duration_minutes,
)


class TestBusinessHours:
    """Tests for BusinessHours dataclass."""

    def test_is_open_when_both_times_set(self):
        """Should be open when both open and close times are set."""
        hours = BusinessHours(open_time=time(9, 0), close_time=time(17, 0))
        assert hours.is_open is True

    def test_is_closed_when_open_time_none(self):
        """Should be closed when open_time is None."""
        hours = BusinessHours(open_time=None, close_time=time(17, 0))
        assert hours.is_open is False

    def test_is_closed_when_close_time_none(self):
        """Should be closed when close_time is None."""
        hours = BusinessHours(open_time=time(9, 0), close_time=None)
        assert hours.is_open is False

    def test_is_closed_when_both_none(self):
        """Should be closed when both times are None."""
        hours = BusinessHours(open_time=None, close_time=None)
        assert hours.is_open is False


class TestGetBusinessHours:
    """Tests for get_business_hours function."""

    def test_returns_hours_for_configured_day(self):
        """Should return business hours for a configured day."""
        config = {
            "business_hours": {
                "monday": {"open": "09:00", "close": "17:00"},
            }
        }
        # Monday
        date = datetime(2026, 1, 19)
        hours = get_business_hours(config, date)
        assert hours.open_time == time(9, 0)
        assert hours.close_time == time(17, 0)
        assert hours.is_open is True

    def test_returns_closed_for_unconfigured_day(self):
        """Should return closed for unconfigured day."""
        config = {"business_hours": {}}
        date = datetime(2026, 1, 19)
        hours = get_business_hours(config, date)
        assert hours.is_open is False

    def test_returns_closed_for_day_with_no_hours(self):
        """Should return closed when day config has no open/close."""
        config = {
            "business_hours": {
                "sunday": {},
            }
        }
        date = datetime(2026, 1, 18)  # Sunday
        hours = get_business_hours(config, date)
        assert hours.is_open is False


class TestGenerateTimeSlots:
    """Tests for generate_time_slots function."""

    def test_generates_correct_slots(self):
        """Should generate correct time slots based on duration."""
        hours = BusinessHours(open_time=time(9, 0), close_time=time(12, 0))
        slots = generate_time_slots(hours, slot_duration_minutes=60)

        assert len(slots) == 3
        assert slots[0] == (time(9, 0), time(10, 0))
        assert slots[1] == (time(10, 0), time(11, 0))
        assert slots[2] == (time(11, 0), time(12, 0))

    def test_returns_empty_when_closed(self):
        """Should return empty list when business is closed."""
        hours = BusinessHours(open_time=None, close_time=None)
        slots = generate_time_slots(hours, slot_duration_minutes=60)
        assert slots == []

    def test_handles_30_minute_slots(self):
        """Should handle 30-minute slot duration."""
        hours = BusinessHours(open_time=time(9, 0), close_time=time(10, 0))
        slots = generate_time_slots(hours, slot_duration_minutes=30)

        assert len(slots) == 2
        assert slots[0] == (time(9, 0), time(9, 30))
        assert slots[1] == (time(9, 30), time(10, 0))

    def test_partial_slot_not_included(self):
        """Should not include partial slots that don't fit."""
        hours = BusinessHours(open_time=time(9, 0), close_time=time(10, 30))
        slots = generate_time_slots(hours, slot_duration_minutes=60)

        # Only 1 full slot fits (9-10), not enough time for second
        assert len(slots) == 1
        assert slots[0] == (time(9, 0), time(10, 0))


class TestParseAppointmentTimes:
    """Tests for parse_appointment_times function."""

    def test_parses_valid_iso_times(self):
        """Should parse valid ISO format times."""
        appt = {
            "startDate": "2026-01-19T09:00:00Z",
            "endDate": "2026-01-19T10:00:00Z",
        }
        result = parse_appointment_times(appt)
        assert result is not None
        start, end = result
        assert start.hour == 9
        assert end.hour == 10

    def test_returns_none_when_start_missing(self):
        """Should return None when startDate is missing."""
        appt = {"endDate": "2026-01-19T10:00:00Z"}
        assert parse_appointment_times(appt) is None

    def test_returns_none_when_end_missing(self):
        """Should return None when endDate is missing."""
        appt = {"startDate": "2026-01-19T09:00:00Z"}
        assert parse_appointment_times(appt) is None

    def test_returns_none_for_invalid_format(self):
        """Should return None for invalid date format."""
        appt = {
            "startDate": "invalid",
            "endDate": "2026-01-19T10:00:00Z",
        }
        assert parse_appointment_times(appt) is None


class TestCheckSlotConflicts:
    """Tests for check_slot_conflicts function."""

    def test_no_conflict_when_no_appointments(self):
        """Should return False when no appointments."""
        result = check_slot_conflicts(
            slot_start=time(9, 0),
            slot_end=time(10, 0),
            date=datetime(2026, 1, 19),
            appointments=[],
            tech_id="tech1",
        )
        assert result is False

    def test_conflict_when_appointment_overlaps(self):
        """Should return True when appointment overlaps slot."""
        appointments = [
            {
                "technicianId": "tech1",
                "startDate": "2026-01-19T09:30:00Z",
                "endDate": "2026-01-19T10:30:00Z",
            }
        ]
        result = check_slot_conflicts(
            slot_start=time(9, 0),
            slot_end=time(10, 0),
            date=datetime(2026, 1, 19),
            appointments=appointments,
            tech_id="tech1",
        )
        assert result is True

    def test_no_conflict_for_different_tech(self):
        """Should return False when appointment is for different tech."""
        appointments = [
            {
                "technicianId": "tech2",
                "startDate": "2026-01-19T09:00:00Z",
                "endDate": "2026-01-19T10:00:00Z",
            }
        ]
        result = check_slot_conflicts(
            slot_start=time(9, 0),
            slot_end=time(10, 0),
            date=datetime(2026, 1, 19),
            appointments=appointments,
            tech_id="tech1",
        )
        assert result is False

    def test_no_conflict_when_appointment_before_slot(self):
        """Should return False when appointment ends before slot starts."""
        appointments = [
            {
                "technicianId": "tech1",
                "startDate": "2026-01-19T07:00:00Z",
                "endDate": "2026-01-19T08:00:00Z",
            }
        ]
        result = check_slot_conflicts(
            slot_start=time(9, 0),
            slot_end=time(10, 0),
            date=datetime(2026, 1, 19),
            appointments=appointments,
            tech_id="tech1",
        )
        assert result is False

    def test_no_conflict_when_appointment_after_slot(self):
        """Should return False when appointment starts after slot ends."""
        appointments = [
            {
                "technicianId": "tech1",
                "startDate": "2026-01-19T11:00:00Z",
                "endDate": "2026-01-19T12:00:00Z",
            }
        ]
        result = check_slot_conflicts(
            slot_start=time(9, 0),
            slot_end=time(10, 0),
            date=datetime(2026, 1, 19),
            appointments=appointments,
            tech_id="tech1",
        )
        assert result is False


class TestCalculateAvailableSlots:
    """Tests for calculate_available_slots function."""

    def test_returns_empty_when_closed(self):
        """Should return empty list when business is closed."""
        config = {"business_hours": {}}
        slots = calculate_available_slots(
            date=datetime(2026, 1, 19),
            tech_ids=["tech1"],
            appointments=[],
            config=config,
        )
        assert slots == []

    def test_returns_all_slots_when_no_appointments(self):
        """Should return all slots when no appointments."""
        config = {
            "business_hours": {
                "monday": {"open": "09:00", "close": "11:00"},
            },
            "default_slot_duration_minutes": 60,
        }
        slots = calculate_available_slots(
            date=datetime(2026, 1, 19),  # Monday
            tech_ids=["tech1"],
            appointments=[],
            config=config,
        )
        assert len(slots) == 2
        assert slots[0].available_techs == 1
        assert "tech1" in slots[0].available_tech_ids

    def test_excludes_slots_with_no_available_techs(self):
        """Should exclude slots where all techs are busy."""
        config = {
            "business_hours": {
                "monday": {"open": "09:00", "close": "11:00"},
            },
            "default_slot_duration_minutes": 60,
        }
        appointments = [
            {
                "technicianId": "tech1",
                "startDate": "2026-01-19T09:00:00Z",
                "endDate": "2026-01-19T10:00:00Z",
            }
        ]
        slots = calculate_available_slots(
            date=datetime(2026, 1, 19),
            tech_ids=["tech1"],
            appointments=appointments,
            config=config,
        )
        # Only 10-11 slot should be available
        assert len(slots) == 1
        assert slots[0].start == time(10, 0)


class TestIsSlotAvailable:
    """Tests for is_slot_available function."""

    def test_available_when_no_conflicts(self):
        """Should return True when no conflicts."""
        is_avail, tech_ids = is_slot_available(
            date=datetime(2026, 1, 19),
            slot_start=time(9, 0),
            slot_end=time(10, 0),
            tech_ids=["tech1", "tech2"],
            appointments=[],
        )
        assert is_avail is True
        assert set(tech_ids) == {"tech1", "tech2"}

    def test_available_when_some_techs_free(self):
        """Should return True when at least one tech is free."""
        appointments = [
            {
                "technicianId": "tech1",
                "startDate": "2026-01-19T09:00:00Z",
                "endDate": "2026-01-19T10:00:00Z",
            }
        ]
        is_avail, tech_ids = is_slot_available(
            date=datetime(2026, 1, 19),
            slot_start=time(9, 0),
            slot_end=time(10, 0),
            tech_ids=["tech1", "tech2"],
            appointments=appointments,
        )
        assert is_avail is True
        assert tech_ids == ["tech2"]

    def test_unavailable_when_all_techs_busy(self):
        """Should return False when all techs are busy."""
        appointments = [
            {
                "technicianId": "tech1",
                "startDate": "2026-01-19T09:00:00Z",
                "endDate": "2026-01-19T10:00:00Z",
            }
        ]
        is_avail, tech_ids = is_slot_available(
            date=datetime(2026, 1, 19),
            slot_start=time(9, 0),
            slot_end=time(10, 0),
            tech_ids=["tech1"],
            appointments=appointments,
        )
        assert is_avail is False
        assert tech_ids == []


class TestGetServiceDurationMinutes:
    """Tests for get_service_duration_minutes function."""

    def test_returns_estimated_duration(self):
        """Should return estimatedDuration when present."""
        service = {"estimatedDuration": 90}
        assert get_service_duration_minutes(service) == 90

    def test_returns_duration_field(self):
        """Should return duration when estimatedDuration not present."""
        service = {"duration": 45}
        assert get_service_duration_minutes(service) == 45

    def test_returns_default_when_no_duration(self):
        """Should return default when no duration field."""
        service = {"name": "Test Service"}
        assert get_service_duration_minutes(service, default_duration=60) == 60

    def test_returns_default_for_invalid_duration(self):
        """Should return default when duration is invalid."""
        service = {"estimatedDuration": "invalid"}
        assert get_service_duration_minutes(service, default_duration=60) == 60
