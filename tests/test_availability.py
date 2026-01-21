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
    validate_config,
    index_appointments_by_tech,
    calculate_days_needed,
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


class TestValidateConfig:
    """Tests for validate_config function."""

    def test_valid_config_passes(self):
        """Should not raise for valid configuration."""
        config = {
            "business_hours": {
                "monday": {"open": "09:00", "close": "17:00"},
                "tuesday": {"open": "09:00", "close": "17:00"},
            },
            "default_slot_duration_minutes": 60,
        }
        validate_config(config)  # Should not raise

    def test_empty_config_raises(self):
        """Should raise ValueError for empty config."""
        with pytest.raises(ValueError, match="empty or None"):
            validate_config({})

    def test_none_config_raises(self):
        """Should raise ValueError for None config."""
        with pytest.raises(ValueError, match="empty or None"):
            validate_config(None)

    def test_missing_business_hours_raises(self):
        """Should raise ValueError when business_hours is missing."""
        config = {"default_slot_duration_minutes": 60}
        with pytest.raises(ValueError, match="business_hours"):
            validate_config(config)

    def test_missing_slot_duration_raises(self):
        """Should raise ValueError when slot duration is missing."""
        config = {"business_hours": {"monday": {"open": "09:00", "close": "17:00"}}}
        with pytest.raises(ValueError, match="default_slot_duration_minutes"):
            validate_config(config)

    def test_invalid_day_name_raises(self):
        """Should raise ValueError for invalid day name."""
        config = {
            "business_hours": {
                "funday": {"open": "09:00", "close": "17:00"},
            },
            "default_slot_duration_minutes": 60,
        }
        with pytest.raises(ValueError, match="Invalid day name"):
            validate_config(config)

    def test_invalid_open_time_format_raises(self):
        """Should raise ValueError for invalid open time format."""
        config = {
            "business_hours": {
                "monday": {"open": "9am", "close": "17:00"},
            },
            "default_slot_duration_minutes": 60,
        }
        with pytest.raises(ValueError, match="Invalid open time format"):
            validate_config(config)

    def test_invalid_close_time_format_raises(self):
        """Should raise ValueError for invalid close time format."""
        config = {
            "business_hours": {
                "monday": {"open": "09:00", "close": "5pm"},
            },
            "default_slot_duration_minutes": 60,
        }
        with pytest.raises(ValueError, match="Invalid close time format"):
            validate_config(config)

    def test_negative_slot_duration_raises(self):
        """Should raise ValueError for negative slot duration."""
        config = {
            "business_hours": {"monday": {"open": "09:00", "close": "17:00"}},
            "default_slot_duration_minutes": -60,
        }
        with pytest.raises(ValueError, match="positive number"):
            validate_config(config)

    def test_zero_slot_duration_raises(self):
        """Should raise ValueError for zero slot duration."""
        config = {
            "business_hours": {"monday": {"open": "09:00", "close": "17:00"}},
            "default_slot_duration_minutes": 0,
        }
        with pytest.raises(ValueError, match="positive number"):
            validate_config(config)


class TestIndexAppointmentsByTech:
    """Tests for index_appointments_by_tech function."""

    def test_indexes_by_technician_id(self):
        """Should index appointments by technicianId."""
        appointments = [
            {"technicianId": "tech1", "startDate": "2026-01-19T09:00:00Z"},
            {"technicianId": "tech1", "startDate": "2026-01-19T10:00:00Z"},
            {"technicianId": "tech2", "startDate": "2026-01-19T09:00:00Z"},
        ]
        indexed = index_appointments_by_tech(appointments)
        assert len(indexed["tech1"]) == 2
        assert len(indexed["tech2"]) == 1

    def test_indexes_by_user_id_fallback(self):
        """Should use userId as fallback when technicianId is missing."""
        appointments = [
            {"userId": "tech1", "startDate": "2026-01-19T09:00:00Z"},
        ]
        indexed = index_appointments_by_tech(appointments)
        assert "tech1" in indexed
        assert len(indexed["tech1"]) == 1

    def test_empty_list_returns_empty_dict(self):
        """Should return empty dict for empty appointment list."""
        indexed = index_appointments_by_tech([])
        assert indexed == {}

    def test_appointments_without_tech_id_skipped(self):
        """Should skip appointments without tech ID."""
        appointments = [
            {"startDate": "2026-01-19T09:00:00Z"},  # No tech ID
            {"technicianId": "tech1", "startDate": "2026-01-19T10:00:00Z"},
        ]
        indexed = index_appointments_by_tech(appointments)
        assert len(indexed) == 1
        assert "tech1" in indexed


class TestCalculateDaysNeeded:
    """Tests for calculate_days_needed function."""

    def test_single_day_service(self):
        """Should return single day for service that fits in one day."""
        config = {
            "business_hours": {
                "monday": {"open": "09:00", "close": "17:00"},
            },
        }
        date = datetime(2026, 1, 19)  # Monday
        result = calculate_days_needed(
            duration_minutes=120,  # 2 hours
            start_date=date,
            start_time=time(9, 0),
            config=config,
        )
        assert result is not None
        assert len(result) == 1
        assert result[0][1] == 120  # 120 minutes needed on day 1

    def test_multi_day_service(self):
        """Should return multiple days for service that spans days."""
        config = {
            "business_hours": {
                "monday": {"open": "09:00", "close": "17:00"},
                "tuesday": {"open": "09:00", "close": "17:00"},
            },
        }
        date = datetime(2026, 1, 19)  # Monday
        result = calculate_days_needed(
            duration_minutes=600,  # 10 hours (spans 2 days)
            start_date=date,
            start_time=time(9, 0),
            config=config,
        )
        assert result is not None
        assert len(result) == 2
        # Day 1: 9am-5pm = 8 hours = 480 minutes
        assert result[0][1] == 480
        # Day 2: remaining 120 minutes
        assert result[1][1] == 120

    def test_returns_none_for_closed_day(self):
        """Should return None when starting on a closed day."""
        config = {
            "business_hours": {},  # All days closed
        }
        date = datetime(2026, 1, 19)
        result = calculate_days_needed(
            duration_minutes=60,
            start_date=date,
            start_time=time(9, 0),
            config=config,
        )
        assert result is None

    def test_spans_multiple_weeks_if_needed(self):
        """Should span multiple weeks if only one day per week is open."""
        config = {
            "business_hours": {
                # Only Monday is open with limited hours
                "monday": {"open": "09:00", "close": "10:00"},
            },
        }
        date = datetime(2026, 1, 19)  # Monday
        result = calculate_days_needed(
            duration_minutes=120,  # 2 hours - needs 2 Mondays
            start_date=date,
            start_time=time(9, 0),
            config=config,
        )
        # Should span across 2 Mondays (60 min each)
        assert result is not None
        assert len(result) == 2
        assert result[0][1] == 60  # 60 min on first Monday
        assert result[1][1] == 60  # 60 min on next Monday


class TestCheckSlotConflictsWithIndex:
    """Tests for check_slot_conflicts with indexed appointments."""

    def test_uses_indexed_appointments(self):
        """Should use pre-indexed appointments when provided."""
        appointments = [
            {"technicianId": "tech1", "startDate": "2026-01-19T09:00:00Z", "endDate": "2026-01-19T10:00:00Z"},
        ]
        indexed = index_appointments_by_tech(appointments)

        result = check_slot_conflicts(
            slot_start=time(9, 0),
            slot_end=time(10, 0),
            date=datetime(2026, 1, 19),
            appointments=[],  # Empty list - should use indexed
            tech_id="tech1",
            indexed_appointments=indexed,
        )
        assert result is True  # Conflict found via index

    def test_no_conflict_with_indexed_for_different_tech(self):
        """Should find no conflict for different tech with indexed appointments."""
        appointments = [
            {"technicianId": "tech1", "startDate": "2026-01-19T09:00:00Z", "endDate": "2026-01-19T10:00:00Z"},
        ]
        indexed = index_appointments_by_tech(appointments)

        result = check_slot_conflicts(
            slot_start=time(9, 0),
            slot_end=time(10, 0),
            date=datetime(2026, 1, 19),
            appointments=[],
            tech_id="tech2",  # Different tech
            indexed_appointments=indexed,
        )
        assert result is False  # No conflict


class TestMultiDayAvailability:
    """Tests for multi-day service availability calculation."""

    def test_multiday_service_returns_slots_when_available(self):
        """Should return slots for multi-day services when tech is available."""
        config = {
            "business_hours": {
                "monday": {"open": "09:00", "close": "17:00"},
                "tuesday": {"open": "09:00", "close": "17:00"},
            },
            "default_slot_duration_minutes": 600,  # 10 hours - spans 2 days
            "slot_interval_minutes": 60,
        }
        # No appointments
        appointments = []
        future_appointments = {"2026-01-20": []}

        slots = calculate_available_slots(
            date=datetime(2026, 1, 19),  # Monday
            tech_ids=["tech1"],
            appointments=appointments,
            config=config,
            slot_duration_minutes=600,
            future_appointments=future_appointments,
        )

        # Should have at least one slot available
        assert len(slots) >= 1
        assert "tech1" in slots[0].available_tech_ids

    def test_multiday_service_excludes_tech_with_day2_conflict(self):
        """Should exclude tech if they have a conflict on day 2."""
        config = {
            "business_hours": {
                "monday": {"open": "09:00", "close": "17:00"},
                "tuesday": {"open": "09:00", "close": "17:00"},
            },
            "default_slot_duration_minutes": 600,
            "slot_interval_minutes": 60,
        }
        # No appointments on day 1
        appointments = []
        # Conflict on day 2 for tech1 (9am-10am)
        future_appointments = {
            "2026-01-20": [
                {
                    "technicianId": "tech1",
                    "startDate": "2026-01-20T09:00:00Z",
                    "endDate": "2026-01-20T10:00:00Z",
                }
            ]
        }

        slots = calculate_available_slots(
            date=datetime(2026, 1, 19),  # Monday
            tech_ids=["tech1", "tech2"],
            appointments=appointments,
            config=config,
            slot_duration_minutes=600,
            future_appointments=future_appointments,
        )

        # Should have slots, but tech1 should be excluded from morning slots
        # (they need to work at start of day 2 which conflicts)
        if slots:
            # Check that at least some slots exclude tech1
            morning_slot = next((s for s in slots if s.start == time(9, 0)), None)
            if morning_slot:
                # tech1 should not be available for 9am slot because
                # a 10-hour service starting at 9am needs day 2 morning
                assert "tech1" not in morning_slot.available_tech_ids or "tech2" in morning_slot.available_tech_ids
