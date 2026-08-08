"""Unit tests for availability calculation logic."""

import sys
from datetime import datetime, time
from pathlib import Path

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from availability import (
    BusinessHours,
    TimeSlot,
    build_appointment_segments,
    calculate_available_slots,
    calculate_days_needed,
    cap_by_concurrency,
    check_slot_availability_for_duration,
    collect_multiday_future_dates,
    count_multiday_overlap_capacity,
    count_overlapping_appointments,
    drop_elapsed_slots,
    generate_slot_start_times,
    generate_time_slots,
    get_buffer_minutes,
    get_buffer_minutes_from_labels,
    get_business_hours,
    get_next_business_day,
    get_overlap_info_for_slot,
    get_service_duration_minutes,
    is_slot_available,
    load_config,
    parse_appointment_times,
    slot_capacity,
    validate_config,
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
            "startDate": "2026-01-19T09:00:00-06:00",
            "endDate": "2026-01-19T10:00:00-06:00",
        }
        result = parse_appointment_times(appt)
        assert result is not None
        start, end = result
        assert start.hour == 9
        assert end.hour == 10

    def test_returns_none_when_start_missing(self):
        """Should return None when startDate is missing."""
        appt = {"endDate": "2026-01-19T10:00:00-06:00"}
        assert parse_appointment_times(appt) is None

    def test_returns_none_when_end_missing(self):
        """Should return None when endDate is missing."""
        appt = {"startDate": "2026-01-19T09:00:00-06:00"}
        assert parse_appointment_times(appt) is None

    def test_returns_none_for_invalid_format(self):
        """Should return None for invalid date format."""
        appt = {
            "startDate": "invalid",
            "endDate": "2026-01-19T10:00:00-06:00",
        }
        assert parse_appointment_times(appt) is None


class TestCountOverlappingAppointments:
    """Tests for count_overlapping_appointments function.

    Replaces the prior per-tech check_slot_conflicts since Shopmonkey
    appointment records do not carry a technicianId - capacity has to be
    counted at the shop level. Only appointments with `orderId` set count
    toward overlap (real customer bookings); time-off blocks without an
    order are intentionally ignored.
    """

    def test_zero_when_no_appointments(self):
        assert (
            count_overlapping_appointments(
                slot_start=time(9, 0),
                slot_end=time(10, 0),
                date=datetime(2026, 1, 19),
                appointments=[],
            )
            == 0
        )

    def test_counts_overlapping_real_booking(self):
        appointments = [
            {
                "orderId": "ord_abc",
                "startDate": "2026-01-19T09:30:00-06:00",
                "endDate": "2026-01-19T10:30:00-06:00",
            }
        ]
        assert (
            count_overlapping_appointments(
                slot_start=time(9, 0),
                slot_end=time(10, 0),
                date=datetime(2026, 1, 19),
                appointments=appointments,
            )
            == 1
        )

    def test_ignores_block_without_order_id(self):
        """This shop-level counter deliberately counts only ticketed work, so
        ticketless blocks don't count here. Whether a block makes a specific
        TECH unavailable is `get_overlap_info_for_slot`'s job, not this one."""
        appointments = [
            {
                # No orderId - this is e.g. "Robert Out" or "Lunch".
                "startDate": "2026-01-19T09:00:00-06:00",
                "endDate": "2026-01-19T17:30:00-06:00",
                "name": "Robert Out",
            }
        ]
        assert (
            count_overlapping_appointments(
                slot_start=time(9, 0),
                slot_end=time(10, 0),
                date=datetime(2026, 1, 19),
                appointments=appointments,
            )
            == 0
        )

    def test_no_overlap_when_appointment_before_slot(self):
        appointments = [
            {
                "orderId": "ord_abc",
                "startDate": "2026-01-19T07:00:00-06:00",
                "endDate": "2026-01-19T08:00:00-06:00",
            }
        ]
        assert (
            count_overlapping_appointments(
                slot_start=time(9, 0),
                slot_end=time(10, 0),
                date=datetime(2026, 1, 19),
                appointments=appointments,
            )
            == 0
        )

    def test_no_overlap_when_appointment_ends_at_slot_start(self):
        """Half-open overlap: an appt ending exactly at slot start is free."""
        appointments = [
            {
                "orderId": "ord_abc",
                "startDate": "2026-01-19T08:00:00-06:00",
                "endDate": "2026-01-19T09:00:00-06:00",
            }
        ]
        assert (
            count_overlapping_appointments(
                slot_start=time(9, 0),
                slot_end=time(10, 0),
                date=datetime(2026, 1, 19),
                appointments=appointments,
            )
            == 0
        )

    def test_skips_booking_with_unparseable_dates(self):
        """A malformed date on a real order must be skipped quietly rather than
        crashing the availability check, and must not inflate the count."""
        appointments = [
            {
                "orderId": "ord_bad",
                "startDate": "not-a-date",
                "endDate": "2026-01-19T10:00:00-06:00",
            },
            {
                "orderId": "ord_ok",
                "startDate": "2026-01-19T09:30:00-06:00",
                "endDate": "2026-01-19T10:30:00-06:00",
            },
        ]
        assert (
            count_overlapping_appointments(
                slot_start=time(9, 0),
                slot_end=time(10, 0),
                date=datetime(2026, 1, 19),
                appointments=appointments,
            )
            == 1
        )

    def test_counts_multiple_overlapping_appointments(self):
        appointments = [
            {
                "orderId": "ord_a",
                "startDate": "2026-01-19T09:00:00-06:00",
                "endDate": "2026-01-19T10:00:00-06:00",
            },
            {
                "orderId": "ord_b",
                "startDate": "2026-01-19T09:30:00-06:00",
                "endDate": "2026-01-19T10:30:00-06:00",
            },
        ]
        assert (
            count_overlapping_appointments(
                slot_start=time(9, 0),
                slot_end=time(10, 0),
                date=datetime(2026, 1, 19),
                appointments=appointments,
            )
            == 2
        )


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
        """Slots with overlap >= qualified tech count are excluded."""
        config = {
            "business_hours": {
                "monday": {"open": "09:00", "close": "11:00"},
            },
            "default_slot_duration_minutes": 60,
        }
        appointments = [
            {
                "orderId": "ord_abc",
                "startDate": "2026-01-19T09:00:00-06:00",
                "endDate": "2026-01-19T10:00:00-06:00",
            }
        ]
        slots = calculate_available_slots(
            date=datetime(2026, 1, 19),
            tech_ids=["tech1"],
            appointments=appointments,
            config=config,
        )
        # 9-10 slot has overlap=1 >= tech_count=1 → excluded. 10-11 free.
        assert len(slots) == 1
        assert slots[0].start == time(10, 0)

    def test_capacity_reflects_remaining_techs(self):
        """With 2 qualified techs and 1 overlap, slot shows capacity=1."""
        config = {
            "business_hours": {
                "monday": {"open": "09:00", "close": "10:00"},
            },
            "default_slot_duration_minutes": 60,
        }
        appointments = [
            {
                "orderId": "ord_abc",
                "startDate": "2026-01-19T09:00:00-06:00",
                "endDate": "2026-01-19T10:00:00-06:00",
            }
        ]
        slots = calculate_available_slots(
            date=datetime(2026, 1, 19),
            tech_ids=["tech1", "tech2"],
            appointments=appointments,
            config=config,
        )
        assert len(slots) == 1
        assert slots[0].available_techs == 1

    def test_blocks_naming_no_tech_do_not_consume_capacity(self):
        """A ticketless entry that names nobody (a shop-wide "Cars & Coffee")
        blocks no one, so the slot stays fully available."""
        config = {
            "business_hours": {
                "monday": {"open": "09:00", "close": "10:00"},
            },
            "default_slot_duration_minutes": 60,
        }
        appointments = [
            {
                # No orderId and no tech assigned.
                "startDate": "2026-01-19T09:00:00-06:00",
                "endDate": "2026-01-19T10:00:00-06:00",
                "name": "Cars & Coffee",
            }
        ]
        slots = calculate_available_slots(
            date=datetime(2026, 1, 19),
            tech_ids=["tech1"],
            appointments=appointments,
            config=config,
        )
        assert len(slots) == 1
        assert slots[0].available_techs == 1

    def test_time_off_block_removes_the_only_tech_and_drops_the_slot(self):
        """The reported bug, end to end: the shop's only qualified tech is out
        on a ticketless block, so the slot must not be offered at all."""
        config = {
            "business_hours": {
                "monday": {"open": "09:00", "close": "10:00"},
            },
            "default_slot_duration_minutes": 60,
        }
        appointments = [
            {
                # "Robert Out"-style block: no orderId, but names the tech.
                "startDate": "2026-01-19T09:00:00-06:00",
                "endDate": "2026-01-19T10:00:00-06:00",
                "name": "Tech Out",
                "_busyTechIds": ["tech1"],
            }
        ]
        slots = calculate_available_slots(
            date=datetime(2026, 1, 19),
            tech_ids=["tech1"],
            appointments=appointments,
            config=config,
        )
        assert slots == []


class TestIsSlotAvailable:
    """Tests for is_slot_available function.

    Returns (is_available, eligible_tech_ids). The eligible list is the
    FULL qualified set when there's remaining capacity (Shopmonkey doesn't
    expose which specific tech is busy from the appointment record);
    empty when the shop is full for that slot.
    """

    def test_available_when_no_conflicts(self):
        is_avail, tech_ids = is_slot_available(
            date=datetime(2026, 1, 19),
            slot_start=time(9, 0),
            slot_end=time(10, 0),
            tech_ids=["tech1", "tech2"],
            appointments=[],
        )
        assert is_avail is True
        assert set(tech_ids) == {"tech1", "tech2"}

    def test_available_when_capacity_remaining(self):
        """1 overlap + 2 qualified techs → 1 tech of capacity remains."""
        appointments = [
            {
                "orderId": "ord_abc",
                "startDate": "2026-01-19T09:00:00-06:00",
                "endDate": "2026-01-19T10:00:00-06:00",
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
        # Full qualified list returned (we can't tell from the API which
        # specific tech is busy; round-robin picks one).
        assert set(tech_ids) == {"tech1", "tech2"}

    def test_unavailable_when_overlap_meets_capacity(self):
        """1 overlap + 1 qualified tech → no capacity."""
        appointments = [
            {
                "orderId": "ord_abc",
                "startDate": "2026-01-19T09:00:00-06:00",
                "endDate": "2026-01-19T10:00:00-06:00",
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

    def test_block_naming_no_tech_does_not_consume_capacity(self):
        """A ticketless entry naming nobody leaves the slot free."""
        appointments = [
            {
                "startDate": "2026-01-19T09:00:00-06:00",
                "endDate": "2026-01-19T10:00:00-06:00",
                "name": "Cars & Coffee",
            }
        ]
        is_avail, tech_ids = is_slot_available(
            date=datetime(2026, 1, 19),
            slot_start=time(9, 0),
            slot_end=time(10, 0),
            tech_ids=["tech1"],
            appointments=appointments,
        )
        assert is_avail is True
        assert tech_ids == ["tech1"]

    def test_time_off_block_fails_the_booking_recheck(self):
        """/book re-validates through this path inside the lock. A tech who
        went out after the slot was advertised must fail the re-check rather
        than get booked."""
        appointments = [
            {
                "startDate": "2026-01-19T09:00:00-06:00",
                "endDate": "2026-01-19T10:00:00-06:00",
                "name": "Tech Out",
                "_busyTechIds": ["tech1"],
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

    def test_sums_labor_hours_into_minutes(self):
        """labors[] is the primary duration source in Shopmonkey. A 1.5h + 0.5h
        service must book a 2-hour window; getting this wrong sizes every slot
        and every created appointment wrongly."""
        service = {"labors": [{"hours": 1.5}, {"hours": 0.5}]}
        assert get_service_duration_minutes(service) == 120

    def test_labor_hours_win_over_duration_fields(self):
        """Shopmonkey's own labor time is authoritative over any stale
        estimatedDuration field left on the record."""
        service = {"labors": [{"hours": 3}], "estimatedDuration": 45}
        assert get_service_duration_minutes(service) == 180

    def test_labor_with_unparseable_hours_is_skipped(self):
        """One junk hours value must not crash the booking path nor discard
        the sibling labor's time."""
        service = {"labors": [{"hours": "abc"}, {"hours": 2}]}
        assert get_service_duration_minutes(service) == 120

    def test_labors_carrying_no_hours_fall_through_to_duration_field(self):
        """Services often carry labors with hours unset. Those must not book a
        zero-length appointment - the duration fields still apply."""
        service = {"labors": [{"hours": None}, {"hours": 0}], "estimatedDuration": 90}
        assert get_service_duration_minutes(service) == 90

    def test_labors_carrying_no_hours_and_no_duration_use_default(self):
        service = {"labors": [{}]}
        assert get_service_duration_minutes(service, default_duration=45) == 45


class TestGetBufferMinutes:
    """Tests for get_buffer_minutes function."""

    def test_returns_buffer_from_label(self):
        """Should return buffer minutes from buffer:X label."""
        service = {
            "name": "Bedliner - Short Bed",
            "labels": [
                {"name": "Bedliner", "color": "blue"},
                {"name": "buffer:180", "color": "gray"},
            ],
        }
        assert get_buffer_minutes(service) == 180

    def test_returns_zero_when_no_buffer_label(self):
        """Should return 0 when no buffer label and no config."""
        service = {
            "name": "Window Tint",
            "labels": [{"name": "Window Tint", "color": "blue"}],
        }
        assert get_buffer_minutes(service) == 0

    def test_returns_zero_when_no_labels(self):
        """Should return 0 when service has no labels."""
        service = {"name": "Unlabeled Service"}
        assert get_buffer_minutes(service) == 0

    def test_handles_buffer_label_case_insensitive(self):
        """Should match buffer label case-insensitively."""
        service = {
            "labels": [{"name": "Buffer:120"}],
        }
        assert get_buffer_minutes(service) == 120

    def test_returns_zero_for_invalid_buffer_value(self):
        """Should return 0 when buffer value is not a valid integer."""
        service = {
            "labels": [{"name": "buffer:invalid"}],
        }
        assert get_buffer_minutes(service) == 0

    def test_returns_buffer_from_config(self):
        """Should return buffer from config when no buffer label."""
        service = {
            "name": "Bedliner - Short Bed",
            "labels": [{"name": "Bedliner", "color": "blue"}],
        }
        config = {
            "service_buffers": {"Bedliner": 180},
        }
        assert get_buffer_minutes(service, config) == 180

    def test_label_overrides_config(self):
        """Service buffer:X label should override config-based buffer."""
        service = {
            "name": "Bedliner - Short Bed",
            "labels": [
                {"name": "Bedliner", "color": "blue"},
                {"name": "buffer:120", "color": "gray"},
            ],
        }
        config = {
            "service_buffers": {"Bedliner": 180},
        }
        # Label says 120, config says 180 - label wins
        assert get_buffer_minutes(service, config) == 120

    def test_config_buffer_multiple_labels(self):
        """Should match first label that has a config buffer."""
        service = {
            "labels": [
                {"name": "Premium", "color": "gold"},
                {"name": "Bedliner", "color": "blue"},
            ],
        }
        config = {
            "service_buffers": {"Bedliner": 180},
        }
        assert get_buffer_minutes(service, config) == 180

    def test_returns_zero_when_config_has_no_matching_buffer(self):
        """Should return 0 when config exists but no matching buffer."""
        service = {
            "labels": [{"name": "Window Tint", "color": "blue"}],
        }
        config = {
            "service_buffers": {"Bedliner": 180},
        }
        assert get_buffer_minutes(service, config) == 0

    def test_unparseable_config_buffer_is_skipped_and_search_continues(self):
        """A typo'd buffer value in config.yaml must not crash the availability
        path, and must not shadow a later label whose buffer is valid."""
        service = {
            "labels": [{"name": "Bedliner"}, {"name": "Window Tint"}],
        }
        config = {
            "service_buffers": {"Bedliner": "three hours", "Window Tint": 45},
        }
        assert get_buffer_minutes(service, config) == 45

    def test_sole_label_with_unparseable_config_buffer_silently_loses_it(self):
        """HAZARD, not a desired behaviour: the production shape is a service
        carrying only its department label. A typo'd value in config.yaml is
        swallowed and the service schedules with NO cure buffer at all - a
        bedliner books back-to-back instead of getting 3 hours of cure time.
        Nothing surfaces the typo; this pins the silent-fallback cost."""
        service = {"labels": [{"name": "Bedliner"}]}
        config = {"service_buffers": {"Bedliner": "three hours"}}
        assert get_buffer_minutes(service, config) == 0


class TestGetBufferMinutesFromLabels:
    """The deprecated alias still has callers' semantics to honour: labels
    only, never config."""

    def test_reads_the_buffer_label(self):
        service = {"labels": [{"name": "Bedliner"}, {"name": "buffer:90"}]}
        assert get_buffer_minutes_from_labels(service) == 90

    def test_never_consults_config_buffers(self):
        """The alias hard-wires config=None, so a department buffer that
        get_buffer_minutes would apply is invisible through this path. Callers
        still on the alias silently lose configured buffers."""
        service = {"labels": [{"name": "Bedliner"}]}
        assert get_buffer_minutes(service, {"service_buffers": {"Bedliner": 180}}) == 180
        assert get_buffer_minutes_from_labels(service) == 0


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

    def test_business_hours_as_list_raises(self):
        """A YAML list under business_hours (a common indentation slip) must be
        rejected with a config error, not blow up later with an AttributeError
        deep inside slot generation."""
        config = {
            "business_hours": ["monday", "tuesday"],
            "default_slot_duration_minutes": 60,
        }
        with pytest.raises(ValueError, match="must be a dictionary"):
            validate_config(config)

    def test_null_day_is_accepted_as_closed(self):
        """config.yaml expresses a closed day as a bare `sunday:` (null). That
        is the documented way to close a day and must validate."""
        config = {
            "business_hours": {
                "monday": {"open": "09:00", "close": "17:00"},
                "sunday": None,
            },
            "default_slot_duration_minutes": 60,
        }
        validate_config(config)  # Should not raise

    def test_scalar_day_hours_raises(self):
        """A day written as a string instead of open/close keys is malformed;
        reject it up front rather than treating the day as closed."""
        config = {
            "business_hours": {"monday": "09:00-17:00"},
            "default_slot_duration_minutes": 60,
        }
        with pytest.raises(ValueError, match="dictionary or null"):
            validate_config(config)


class TestLoadConfig:
    """load_config is the single path that reads config.yaml at startup."""

    def test_parses_yaml_into_a_validatable_config(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(
            "business_hours:\n"
            "  monday:\n"
            "    open: '09:00'\n"
            "    close: '17:00'\n"
            "  sunday:\n"
            "default_slot_duration_minutes: 60\n"
        )
        config = load_config(str(path))
        assert config["default_slot_duration_minutes"] == 60
        assert config["business_hours"]["monday"] == {"open": "09:00", "close": "17:00"}
        assert config["business_hours"]["sunday"] is None

    def test_missing_file_raises(self, tmp_path):
        """A missing config must fail loudly at startup; silently returning an
        empty config would report every day closed."""
        with pytest.raises(FileNotFoundError):
            load_config(str(tmp_path / "does-not-exist.yaml"))


class TestGetNextBusinessDay:
    """Multi-day services roll forward through this helper; returning a closed
    day would schedule work on a day nobody is in the shop."""

    WEEKDAYS = {
        "business_hours": {
            "monday": {"open": "09:00", "close": "17:00"},
            "tuesday": {"open": "09:00", "close": "17:00"},
            "wednesday": {"open": "09:00", "close": "17:00"},
            "thursday": {"open": "09:00", "close": "17:00"},
            "friday": {"open": "09:00", "close": "17:00"},
        }
    }

    def test_skips_the_weekend(self):
        # Friday 2026-01-23 -> Monday 2026-01-26.
        assert get_next_business_day(datetime(2026, 1, 23), self.WEEKDAYS) == datetime(2026, 1, 26)

    def test_reaches_a_day_a_full_week_out(self):
        """With only Mondays open the answer sits at the far edge of the
        7-day lookahead; a shorter search would wrongly report None."""
        config = {"business_hours": {"monday": {"open": "09:00", "close": "17:00"}}}
        assert get_next_business_day(datetime(2026, 1, 19), config) == datetime(2026, 1, 26)

    def test_returns_none_when_never_open(self):
        """No open day in range -> None, so callers refuse the booking instead
        of picking an arbitrary closed date."""
        assert get_next_business_day(datetime(2026, 1, 19), {"business_hours": {}}) is None


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

    def test_multiday_day2_overlap_reduces_capacity(self):
        """Day-2 overlap reduces a multi-day slot's available_techs."""
        config = {
            "business_hours": {
                "monday": {"open": "09:00", "close": "17:00"},
                "tuesday": {"open": "09:00", "close": "17:00"},
            },
            "default_slot_duration_minutes": 600,
            "slot_interval_minutes": 60,
        }
        appointments = []
        # One overlapping order on day 2 morning - reduces capacity by 1.
        future_appointments = {
            "2026-01-20": [
                {
                    "orderId": "ord_day2",
                    "startDate": "2026-01-20T15:00:00Z",  # 9am CST
                    "endDate": "2026-01-20T16:00:00Z",
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

        morning_slot = next((s for s in slots if s.start == time(9, 0)), None)
        # 2 qualified techs - 1 day-2 overlap = 1 remaining capacity.
        assert morning_slot is not None
        assert morning_slot.available_techs == 1

    def test_multiday_day2_full_blocks_slot(self):
        """When day-2 overlap >= tech count, slot is unavailable."""
        config = {
            "business_hours": {
                "monday": {"open": "09:00", "close": "17:00"},
                "tuesday": {"open": "09:00", "close": "17:00"},
            },
            "default_slot_duration_minutes": 600,
            "slot_interval_minutes": 60,
        }
        future_appointments = {
            "2026-01-20": [
                {
                    "orderId": "ord_a",
                    "startDate": "2026-01-20T15:00:00Z",
                    "endDate": "2026-01-20T16:00:00Z",
                },
                {
                    "orderId": "ord_b",
                    "startDate": "2026-01-20T15:00:00Z",
                    "endDate": "2026-01-20T16:00:00Z",
                },
            ]
        }
        slots = calculate_available_slots(
            date=datetime(2026, 1, 19),
            tech_ids=["tech1", "tech2"],
            appointments=[],
            config=config,
            slot_duration_minutes=600,
            future_appointments=future_appointments,
        )
        morning_slot = next((s for s in slots if s.start == time(9, 0)), None)
        # 2 techs - 2 overlaps = 0 capacity; slot dropped from list.
        assert morning_slot is None


class TestPerTechAvailability:
    """Verify that labor.technicianId info on appointments lets us drop
    only the specifically-busy techs from the qualified pool, instead of
    treating every overlap as a shop-wide capacity hit.

    `_busyTechIds` is populated upstream by walking Appointment → Order →
    Service.labors → technicianId (see ShopmonkeyClient.get_busy_techs_
    for_appointments). Tests pre-populate it directly.
    """

    def _appt(
        self,
        order_id: str,
        start: str,
        end: str,
        busy: list[str] | None = None,
    ):
        a = {"orderId": order_id, "startDate": start, "endDate": end}
        if busy is not None:
            a["_busyTechIds"] = busy
        return a

    def test_get_overlap_info_returns_specific_busy_techs(self):
        appts = [
            self._appt(
                "ord_a",
                "2026-01-19T09:00:00-06:00",
                "2026-01-19T10:00:00-06:00",
                busy=["tech_alex"],
            )
        ]
        unavailable, working, unattributed = get_overlap_info_for_slot(
            time(9, 0), time(10, 0), datetime(2026, 1, 19), appts
        )
        assert unavailable == {"tech_alex"}
        # On a ticket, so they also occupy a bay.
        assert working == {"tech_alex"}
        assert unattributed == 0

    def test_unattributed_overlap_when_no_busy_tech_ids(self):
        """Overlapping order with no tech named in either source counts as 1
        unattributed."""
        appts = [
            self._appt(
                "ord_a",
                "2026-01-19T09:00:00-06:00",
                "2026-01-19T10:00:00-06:00",
                busy=[],
            )
        ]
        unavailable, working, unattributed = get_overlap_info_for_slot(
            time(9, 0), time(10, 0), datetime(2026, 1, 19), appts
        )
        assert unavailable == set()
        assert working == set()
        assert unattributed == 1

    def test_time_off_block_makes_its_tech_unavailable_without_holding_a_bay(self):
        """The bug this fixes: a tech assigned to a ticketless calendar entry
        (vacation, "Mina out") was invisible to the scheduler. They must now
        leave the pool - but without consuming department concurrency, since
        an absent tech frees their bay rather than occupying it."""
        appts = [
            {
                "startDate": "2026-01-19T09:00:00-06:00",
                "endDate": "2026-01-19T10:00:00-06:00",
                "_busyTechIds": ["tech_mina"],
                # No orderId: this is "Mina out", not a work order.
            }
        ]
        unavailable, working, unattributed = get_overlap_info_for_slot(
            time(9, 0), time(10, 0), datetime(2026, 1, 19), appts
        )
        assert unavailable == {"tech_mina"}
        assert working == set()
        assert unattributed == 0

    def test_ticketless_entry_naming_no_tech_is_ignored(self):
        """Shop-wide entries ("Cars & Coffee") name nobody and carry no order.
        There is no one to block, and they must not silently eat capacity."""
        appts = [
            {
                "startDate": "2026-01-19T09:00:00-06:00",
                "endDate": "2026-01-19T10:00:00-06:00",
                "_busyTechIds": [],
            }
        ]
        unavailable, working, unattributed = get_overlap_info_for_slot(
            time(9, 0), time(10, 0), datetime(2026, 1, 19), appts
        )
        assert unavailable == set()
        assert working == set()
        assert unattributed == 0

    def test_entry_with_unparseable_dates_is_skipped(self):
        """Ticketless entries now flow through this loop for the first time,
        so a malformed/missing date must be skipped quietly - not crash the
        availability check and not block the tech it names."""
        appts = [
            {"_busyTechIds": ["tech_alex"]},  # no dates at all
            {
                "startDate": "not-a-date",
                "endDate": "2026-01-19T10:00:00-06:00",
                "_busyTechIds": ["tech_cam"],
            },
        ]
        unavailable, working, unattributed = get_overlap_info_for_slot(
            time(9, 0), time(10, 0), datetime(2026, 1, 19), appts
        )
        assert unavailable == set()
        assert working == set()
        assert unattributed == 0

    def test_slot_capacity_drops_tech_on_time_off(self):
        """End-to-end through slot_capacity: the tech on PTO is not offered."""
        appts = [
            {
                "startDate": "2026-01-19T09:00:00-06:00",
                "endDate": "2026-01-19T10:00:00-06:00",
                "_busyTechIds": ["tech_alex"],
            }
        ]
        capacity, free = slot_capacity(
            time(9, 0),
            time(10, 0),
            datetime(2026, 1, 19),
            appts,
            ["tech_alex", "tech_cam", "tech_dave"],
        )
        assert capacity == 2
        assert set(free) == {"tech_cam", "tech_dave"}

    def test_time_off_does_not_consume_department_concurrency(self):
        """A tech out on PTO holds no bay, so a department capped at 2
        simultaneous services still has both slots open to the techs who
        are actually present."""
        appts = [
            {
                "startDate": "2026-01-19T09:00:00-06:00",
                "endDate": "2026-01-19T10:00:00-06:00",
                "_busyTechIds": ["tech_alex"],  # out; no orderId
            }
        ]
        capacity, free = slot_capacity(
            time(9, 0),
            time(10, 0),
            datetime(2026, 1, 19),
            appts,
            ["tech_alex", "tech_cam", "tech_dave"],
            max_concurrency=2,
        )
        # Were absence miscounted as bay occupancy, this would cap at 1.
        assert capacity == 2
        assert set(free) == {"tech_cam", "tech_dave"}

    def test_slot_capacity_drops_only_busy_tech(self):
        """A Window Tint tech busy on a Body Shop appointment is dropped
        from the Window Tint qualified pool. Cross-department tech
        constraints are captured here even though the appointment is for
        a different department."""
        appts = [
            self._appt(
                "ord_a",
                "2026-01-19T09:00:00-06:00",
                "2026-01-19T10:00:00-06:00",
                busy=["tech_alex"],
            )
        ]
        capacity, free = slot_capacity(
            time(9, 0),
            time(10, 0),
            datetime(2026, 1, 19),
            appts,
            ["tech_alex", "tech_cam", "tech_dave"],
        )
        # 3 qualified - tech_alex busy = 2 free, 0 unattributed → capacity 2
        assert capacity == 2
        assert "tech_alex" not in free
        assert set(free) == {"tech_cam", "tech_dave"}

    def test_busy_tech_outside_qualified_pool_does_not_reduce_capacity(self):
        """An appointment busying a tech NOT qualified for this department
        leaves the pool fully available."""
        appts = [
            self._appt(
                "ord_a",
                "2026-01-19T09:00:00-06:00",
                "2026-01-19T10:00:00-06:00",
                busy=["tech_marvin_bodyshop"],
            )
        ]
        capacity, free = slot_capacity(
            time(9, 0),
            time(10, 0),
            datetime(2026, 1, 19),
            appts,
            ["tech_alex", "tech_cam"],
        )
        # marvin isn't in the qualified pool, so dropping him doesn't matter
        assert capacity == 2
        assert set(free) == {"tech_alex", "tech_cam"}

    def test_mix_of_busy_and_unattributed_appointments(self):
        """Specifically-busy techs are dropped AND unattributed overlap
        reduces remaining capacity by 1 each."""
        appts = [
            self._appt(
                "ord_a",
                "2026-01-19T09:00:00-06:00",
                "2026-01-19T10:00:00-06:00",
                busy=["tech_alex"],
            ),
            self._appt(
                "ord_b",
                "2026-01-19T09:00:00-06:00",
                "2026-01-19T10:00:00-06:00",
                busy=[],  # labor walk found no tech
            ),
        ]
        capacity, free = slot_capacity(
            time(9, 0),
            time(10, 0),
            datetime(2026, 1, 19),
            appts,
            ["tech_alex", "tech_cam", "tech_dave"],
        )
        # 3 qualified - tech_alex = 2 free, - 1 unattributed = 1 capacity
        assert capacity == 1
        assert "tech_alex" not in free


class TestTimezoneAwareConflictDetection:
    """Regression coverage for the booking-vs-availability TZ mismatch.

    Pre-fix, the conflict check stripped tzinfo without converting, so a
    UTC appointment time was compared as-if-naive against a slot expressed
    in business-local time. That allowed double-bookings when Shopmonkey
    appointments existed in CDT/CST: a 9am Central appointment came back
    as 14:00 or 15:00 UTC, was treated as 14:00 local, and didn't appear
    to overlap a 9am slot.
    """

    def test_cdt_appointment_counts_against_local_slot_in_may(self):
        # 9am CDT on May 20, 2026 → 14:00 UTC. Shopmonkey returns it with a
        # Z suffix; counting must convert before comparing.
        appointments = [
            {
                "orderId": "ord_abc",
                "startDate": "2026-05-20T14:00:00Z",  # 9am CDT
                "endDate": "2026-05-20T15:30:00Z",  # 10:30am CDT
            }
        ]
        count = count_overlapping_appointments(
            slot_start=time(9, 0),
            slot_end=time(10, 30),
            date=datetime(2026, 5, 20),
            appointments=appointments,
        )
        assert count == 1

    def test_cst_appointment_counts_against_local_slot_in_january(self):
        # 9am CST on Jan 19, 2026 → 15:00 UTC (CST is -06:00).
        appointments = [
            {
                "orderId": "ord_abc",
                "startDate": "2026-01-19T15:00:00Z",
                "endDate": "2026-01-19T16:00:00Z",
            }
        ]
        count = count_overlapping_appointments(
            slot_start=time(9, 0),
            slot_end=time(10, 0),
            date=datetime(2026, 1, 19),
            appointments=appointments,
        )
        assert count == 1

    def test_appointment_outside_business_hours_does_not_count(self):
        # 4am CDT on May 20 (UTC 09:00) shouldn't conflict with a 9am local slot.
        appointments = [
            {
                "orderId": "ord_abc",
                "startDate": "2026-05-20T09:00:00Z",  # 4am CDT
                "endDate": "2026-05-20T10:00:00Z",  # 5am CDT
            }
        ]
        count = count_overlapping_appointments(
            slot_start=time(9, 0),
            slot_end=time(10, 0),
            date=datetime(2026, 5, 20),
            appointments=appointments,
        )
        assert count == 0

    def test_is_slot_available_respects_business_tz(self):
        # Two overlapping bookings 9-10am CDT (14-15 UTC). With 2 qualified
        # techs, capacity goes to 0 and the slot is reported full.
        appointments = [
            {
                "orderId": "ord_a",
                "startDate": "2026-05-20T14:00:00Z",
                "endDate": "2026-05-20T15:00:00Z",
            },
            {
                "orderId": "ord_b",
                "startDate": "2026-05-20T14:00:00Z",
                "endDate": "2026-05-20T15:00:00Z",
            },
        ]
        is_avail, available = is_slot_available(
            date=datetime(2026, 5, 20),
            slot_start=time(9, 0),
            slot_end=time(10, 0),
            tech_ids=["tech1", "tech2"],
            appointments=appointments,
            config={"timezone": "America/Chicago"},
        )
        assert is_avail is False
        assert available == []

    def test_non_default_tz_is_respected(self):
        # Eastern time: 9am ET = 13:00 UTC. Verify config drives the offset.
        appointments = [
            {
                "orderId": "ord_abc",
                "startDate": "2026-05-20T13:00:00Z",
                "endDate": "2026-05-20T14:00:00Z",
            }
        ]
        is_avail, _ = is_slot_available(
            date=datetime(2026, 5, 20),
            slot_start=time(9, 0),
            slot_end=time(10, 0),
            tech_ids=["tech1"],
            appointments=appointments,
            config={"timezone": "America/New_York"},
        )
        assert is_avail is False


class TestCollectMultidayFutureDates:
    """Tests for collect_multiday_future_dates - which continuation days
    /availability must fetch so multi-day capacity checks see real data.

    Regression context (Anne's June 3 report): a 157-minute tint starting
    30 minutes before close is multi-day, but the old `> 300 minutes` gate
    never fetched day-2 appointments for it.
    """

    CONFIG = {
        "business_hours": {
            "monday": {"open": "09:00", "close": "17:00"},
            "tuesday": {"open": "09:00", "close": "17:00"},
            "wednesday": {"open": "09:00", "close": "17:00"},
            "thursday": {"open": "09:00", "close": "17:00"},
            "friday": {"open": "09:00", "close": "17:00"},
        },
        "default_slot_duration_minutes": 60,
        "slot_interval_minutes": 60,
    }

    def test_short_service_needs_no_future_dates(self):
        """A 60-min service fits after every hourly start (last start 16:00)."""
        result = collect_multiday_future_dates(
            datetime(2026, 1, 19),
            60,
            self.CONFIG,  # Monday
        )
        assert result == []

    def test_service_under_5h_rolling_past_close_needs_next_day(self):
        """157 min from the 16:00 start rolls into Tuesday - even though it
        is far below the old 5-hour threshold."""
        result = collect_multiday_future_dates(
            datetime(2026, 1, 19),
            157,
            self.CONFIG,  # Monday
        )
        assert result == ["2026-01-20"]

    def test_friday_rollover_skips_weekend(self):
        """Friday rollover lands on Monday, not Saturday."""
        result = collect_multiday_future_dates(
            datetime(2026, 1, 23),
            157,
            self.CONFIG,  # Friday
        )
        assert result == ["2026-01-26"]  # Monday

    def test_long_service_collects_union_across_starts(self):
        """A 20-hour service: 09:00 start needs Tue+Wed, 16:00 start needs
        Tue+Wed+Thu. Union covers all three."""
        result = collect_multiday_future_dates(
            datetime(2026, 1, 19),
            1200,
            self.CONFIG,  # Monday
        )
        assert result == ["2026-01-20", "2026-01-21", "2026-01-22"]

    def test_closed_day_returns_empty(self):
        result = collect_multiday_future_dates(
            datetime(2026, 1, 24),
            157,
            self.CONFIG,  # Saturday
        )
        assert result == []


class TestGenerateSlotStartTimes:
    """Start times drive which slots are offered at all."""

    def test_returns_empty_when_closed(self):
        """A closed day must yield no start times; without the guard the loop
        would build slots out of None open/close times."""
        assert generate_slot_start_times(BusinessHours(None, None), 60) == []

    def test_starts_are_interval_spaced_and_stop_before_close(self):
        """The last start must be strictly before close - a start AT close
        would advertise a slot with no time left in the day."""
        starts = generate_slot_start_times(BusinessHours(time(9, 0), time(11, 0)), 30)
        assert starts == [time(9, 0), time(9, 30), time(10, 0), time(10, 30)]


class TestCountMultidayOverlapCapacity:
    """Direct tests for the multi-day capacity helper's degenerate inputs,
    which silently zero out capacity rather than raising."""

    CONFIG = {
        "business_hours": {
            "monday": {"open": "09:00", "close": "17:00"},
            "tuesday": {"open": "09:00", "close": "17:00"},
        },
        "default_slot_duration_minutes": 60,
    }

    def test_empty_days_needed_has_no_capacity(self):
        """An empty segmentation describes no bookable time at all; it must not
        fall through and report the first day's free techs as available."""
        capacity, free = count_multiday_overlap_capacity(
            days_needed=[],
            first_day_appointments=[],
            first_day_start_time=time(9, 0),
            first_day_close_time=time(17, 0),
            future_appointments={},
            config=self.CONFIG,
            tech_ids=["tech1", "tech2"],
        )
        assert capacity == 0
        assert free == []

    def test_continuation_day_that_is_closed_has_no_capacity(self):
        """If a spanned day is closed there are no working hours to finish in,
        so the slot must report zero capacity - never treat a closed day as
        wide open and let the booking through."""
        days_needed = [
            (datetime(2026, 1, 19), 480),  # Monday, open
            (datetime(2026, 1, 24), 60),  # Saturday, closed
        ]
        capacity, free = count_multiday_overlap_capacity(
            days_needed=days_needed,
            first_day_appointments=[],
            first_day_start_time=time(9, 0),
            first_day_close_time=time(17, 0),
            future_appointments={},
            config=self.CONFIG,
            tech_ids=["tech1", "tech2"],
        )
        assert capacity == 0
        assert free == []


class TestCheckSlotAvailabilityForDuration:
    """Tests for the /book-side re-check that derives the true span from
    the service duration instead of trusting the client's slot_end."""

    CONFIG = {
        "business_hours": {
            "monday": {"open": "09:00", "close": "17:00"},
            "tuesday": {"open": "09:00", "close": "17:00"},
        },
        "default_slot_duration_minutes": 60,
        "slot_interval_minutes": 60,
    }

    def test_single_day_slot_available(self):
        ok, free, days = check_slot_availability_for_duration(
            date=datetime(2026, 1, 19),
            slot_start=time(9, 0),
            duration_minutes=60,
            tech_ids=["tech1"],
            appointments=[],
            future_appointments={},
            config=self.CONFIG,
        )
        assert ok is True
        assert free == ["tech1"]
        assert len(days) == 1

    def test_single_day_conflict_blocks(self):
        appointments = [
            {
                "orderId": "ord_x",
                "startDate": "2026-01-19T15:00:00Z",  # 09:00 CST
                "endDate": "2026-01-19T16:00:00Z",
            }
        ]
        ok, free, _ = check_slot_availability_for_duration(
            date=datetime(2026, 1, 19),
            slot_start=time(9, 0),
            duration_minutes=60,
            tech_ids=["tech1"],
            appointments=appointments,
            future_appointments={},
            config=self.CONFIG,
        )
        assert ok is False
        assert free == []

    def test_multiday_slot_available_returns_segmentation(self):
        """157 min at 16:00 Monday: 60 min day 1 + 97 min Tuesday."""
        ok, free, days = check_slot_availability_for_duration(
            date=datetime(2026, 1, 19),
            slot_start=time(16, 0),
            duration_minutes=157,
            tech_ids=["tech1"],
            appointments=[],
            future_appointments={"2026-01-20": []},
            config=self.CONFIG,
        )
        assert ok is True
        assert free == ["tech1"]
        assert len(days) == 2
        assert days[0][1] == 60
        assert days[1][1] == 97

    def test_multiday_blocked_when_day2_full(self):
        """Two unattributed day-2 overlaps consume both techs' capacity."""
        future = {
            "2026-01-20": [
                {
                    "orderId": "ord_a",
                    "startDate": "2026-01-20T15:00:00Z",  # 09:00 CST
                    "endDate": "2026-01-20T16:00:00Z",
                },
                {
                    "orderId": "ord_b",
                    "startDate": "2026-01-20T15:00:00Z",
                    "endDate": "2026-01-20T16:00:00Z",
                },
            ]
        }
        ok, free, days = check_slot_availability_for_duration(
            date=datetime(2026, 1, 19),
            slot_start=time(16, 0),
            duration_minutes=157,
            tech_ids=["tech1", "tech2"],
            appointments=[],
            future_appointments=future,
            config=self.CONFIG,
        )
        assert ok is False
        assert free == []
        assert len(days) == 2  # segmentation still reported for logging

    def test_multiday_drops_only_the_busy_tech_on_day2(self):
        """A day-2 booking attributed to tech1 leaves tech2 free."""
        future = {
            "2026-01-20": [
                {
                    "orderId": "ord_a",
                    "startDate": "2026-01-20T15:00:00Z",
                    "endDate": "2026-01-20T16:00:00Z",
                    "_busyTechIds": ["tech1"],
                }
            ]
        }
        ok, free, _ = check_slot_availability_for_duration(
            date=datetime(2026, 1, 19),
            slot_start=time(16, 0),
            duration_minutes=157,
            tech_ids=["tech1", "tech2"],
            appointments=[],
            future_appointments=future,
            config=self.CONFIG,
        )
        assert ok is True
        assert free == ["tech2"]

    def test_multiday_tech_on_time_off_day2_is_not_offered(self):
        """A multi-day service needs the same tech free on EVERY spanned day.
        tech1 is out on day 2 via a ticketless block, so only tech2 can take
        it - previously the block was invisible and tech1 stayed eligible."""
        future = {
            "2026-01-20": [
                {
                    # No orderId: "tech1 out", not a work order.
                    "startDate": "2026-01-20T15:00:00Z",
                    "endDate": "2026-01-20T16:00:00Z",
                    "_busyTechIds": ["tech1"],
                }
            ]
        }
        ok, free, _ = check_slot_availability_for_duration(
            date=datetime(2026, 1, 19),
            slot_start=time(16, 0),
            duration_minutes=157,
            tech_ids=["tech1", "tech2"],
            appointments=[],
            future_appointments=future,
            config=self.CONFIG,
        )
        assert ok is True
        assert free == ["tech2"]

    def test_multiday_blocked_when_sole_tech_out_on_day2(self):
        """With no one else qualified, day-2 time off kills the slot."""
        future = {
            "2026-01-20": [
                {
                    "startDate": "2026-01-20T15:00:00Z",
                    "endDate": "2026-01-20T16:00:00Z",
                    "_busyTechIds": ["tech1"],
                }
            ]
        }
        ok, free, _ = check_slot_availability_for_duration(
            date=datetime(2026, 1, 19),
            slot_start=time(16, 0),
            duration_minutes=157,
            tech_ids=["tech1"],
            appointments=[],
            future_appointments=future,
            config=self.CONFIG,
        )
        assert ok is False
        assert free == []

    def test_closed_day_returns_unavailable(self):
        ok, free, days = check_slot_availability_for_duration(
            date=datetime(2026, 1, 24),  # Saturday
            slot_start=time(9, 0),
            duration_minutes=60,
            tech_ids=["tech1"],
            appointments=[],
            future_appointments={},
            config=self.CONFIG,
        )
        assert ok is False
        assert free == []
        assert days == []


class TestConcurrencyCap:
    """Tests for the per-department max-concurrency occupancy ceiling.

    The cap models a physical resource (bays/equipment): a department may run
    at most `max_concurrency` services at once. Effective capacity is
    `min(free_qualified_techs, max_concurrency - overlapping_dept_bookings)`.
    """

    def _busy_appt(self, busy):
        return {
            "orderId": "ord_x",
            "startDate": "2026-01-19T09:00:00-06:00",
            "endDate": "2026-01-19T10:00:00-06:00",
            "_busyTechIds": busy,
        }

    def test_cap_helper_none_is_passthrough(self):
        assert cap_by_concurrency(3, 0, None) == 3

    def test_cap_helper_binds_to_max(self):
        # 4 techs free, max 1, no bays occupied -> offer 1.
        assert cap_by_concurrency(4, 0, 1) == 1

    def test_cap_helper_shrinks_as_dept_fills(self):
        # 3 free, 1 bay occupied, max 2 -> remaining = 2-1 = 1.
        assert cap_by_concurrency(3, 1, 2) == 1

    def test_cap_helper_zero_when_dept_full(self):
        # 3 free but max 1 already consumed by the 1 working tech -> 0.
        assert cap_by_concurrency(3, 1, 1) == 0

    def test_cap_helper_noop_when_max_exceeds_techs(self):
        assert cap_by_concurrency(4, 0, 10) == 4

    def test_cap_helper_never_returns_negative(self):
        # More bays occupied than the cap allows (data drift) -> 0, not -1.
        assert cap_by_concurrency(3, 3, 2) == 0

    CONFIG = {
        "business_hours": {"monday": {"open": "09:00", "close": "11:00"}},
        "default_slot_duration_minutes": 60,
    }

    def test_available_slots_capped_with_no_bookings(self):
        """4 qualified techs, max 2, empty calendar -> every slot offers 2."""
        slots = calculate_available_slots(
            date=datetime(2026, 1, 19),  # Monday
            tech_ids=["t1", "t2", "t3", "t4"],
            appointments=[],
            config=self.CONFIG,
            max_concurrency=2,
        )
        assert len(slots) == 2
        assert all(s.available_techs == 2 for s in slots)

    def test_available_slots_occupancy_shrinks_remaining(self):
        """A single overlapping dept booking drops the 9-10 slot to 1."""
        slots = calculate_available_slots(
            date=datetime(2026, 1, 19),
            tech_ids=["t1", "t2", "t3", "t4"],
            appointments=[self._busy_appt(["t1"])],  # busies t1, 9-10
            config=self.CONFIG,
            max_concurrency=2,
        )
        nine = next(s for s in slots if s.start == time(9, 0))
        ten = next(s for s in slots if s.start == time(10, 0))
        # 9-10: 2 cap - 1 busy = 1. 10-11: no overlap -> full cap 2.
        assert nine.available_techs == 1
        assert ten.available_techs == 2

    def test_available_slots_full_when_cap_consumed(self):
        """Two overlapping dept bookings hit max=2 -> 9-10 slot disappears."""
        slots = calculate_available_slots(
            date=datetime(2026, 1, 19),
            tech_ids=["t1", "t2", "t3", "t4"],
            appointments=[self._busy_appt(["t1"]), self._busy_appt(["t2"])],
            config=self.CONFIG,
            max_concurrency=2,
        )
        assert all(s.start != time(9, 0) for s in slots)

    def test_no_cap_leaves_full_tech_capacity(self):
        slots = calculate_available_slots(
            date=datetime(2026, 1, 19),
            tech_ids=["t1", "t2", "t3", "t4"],
            appointments=[],
            config=self.CONFIG,
            max_concurrency=None,
        )
        assert all(s.available_techs == 4 for s in slots)

    BOOK_CONFIG = {
        "business_hours": {
            "monday": {"open": "09:00", "close": "17:00"},
            "tuesday": {"open": "09:00", "close": "17:00"},
        },
        "default_slot_duration_minutes": 60,
        "slot_interval_minutes": 60,
    }

    def test_book_recheck_allows_under_cap(self):
        ok, free, _ = check_slot_availability_for_duration(
            date=datetime(2026, 1, 19),
            slot_start=time(9, 0),
            duration_minutes=60,
            tech_ids=["t1", "t2", "t3"],
            appointments=[],
            future_appointments={},
            config=self.BOOK_CONFIG,
            max_concurrency=1,
        )
        assert ok is True
        assert free == ["t1", "t2", "t3"]

    def test_book_recheck_blocks_when_cap_reached(self):
        """max=1 and one dept booking already overlaps -> slot is full."""
        ok, free, _ = check_slot_availability_for_duration(
            date=datetime(2026, 1, 19),
            slot_start=time(9, 0),
            duration_minutes=60,
            tech_ids=["t1", "t2", "t3"],
            appointments=[self._busy_appt(["t1"])],
            future_appointments={},
            config=self.BOOK_CONFIG,
            max_concurrency=1,
        )
        assert ok is False
        assert free == []

    def test_multiday_cap_allows_under_ceiling(self):
        """157 min at 16:00 Mon spans into Tue; max=1, empty calendar -> ok."""
        ok, free, days = check_slot_availability_for_duration(
            date=datetime(2026, 1, 19),
            slot_start=time(16, 0),
            duration_minutes=157,
            tech_ids=["t1", "t2", "t3"],
            appointments=[],
            future_appointments={"2026-01-20": []},
            config=self.BOOK_CONFIG,
            max_concurrency=1,
        )
        assert ok is True
        assert free == ["t1", "t2", "t3"]
        assert len(days) == 2

    def test_multiday_cap_full_via_day2_occupancy(self):
        """A day-2 dept booking consumes the only concurrency slot (max=1)
        -> the bottleneck (day 2) governs and the multi-day slot is blocked,
        even though day 1 is wide open."""
        future = {
            "2026-01-20": [
                {
                    "orderId": "ord_day2",
                    "startDate": "2026-01-20T15:00:00Z",  # 09:00 CST, in the day-2 window
                    "endDate": "2026-01-20T16:00:00Z",
                    "_busyTechIds": ["t1"],
                }
            ]
        }
        ok, free, days = check_slot_availability_for_duration(
            date=datetime(2026, 1, 19),
            slot_start=time(16, 0),
            duration_minutes=157,
            tech_ids=["t1", "t2", "t3"],
            appointments=[],
            future_appointments=future,
            config=self.BOOK_CONFIG,
            max_concurrency=1,
        )
        # Day 1: 3 free, 0 busy, remaining = 1. Day 2: 2 free, 1 busy in dept,
        # remaining = 1-1 = 0. min across days = 0 -> blocked.
        assert ok is False
        assert free == []
        assert len(days) == 2

    def test_unattributed_overlap_not_double_counted_against_cap(self):
        """An unattributed overlap (orderId, no _busyTechIds) reduces the
        free-tech arm but NOT the department-occupancy arm. With 4 techs and
        max=2, one unattributed overlap leaves min(4-1, 2-0)=2, not 1."""
        unattributed = {
            "orderId": "ord_u",
            "startDate": "2026-01-19T09:00:00-06:00",
            "endDate": "2026-01-19T10:00:00-06:00",
            "_busyTechIds": [],
        }
        slots = calculate_available_slots(
            date=datetime(2026, 1, 19),
            tech_ids=["t1", "t2", "t3", "t4"],
            appointments=[unattributed],
            config=self.CONFIG,
            max_concurrency=2,
        )
        nine = next(s for s in slots if s.start == time(9, 0))
        assert nine.available_techs == 2


class TestBuildAppointmentSegments:
    """Tests for converting a days_needed segmentation into concrete
    per-day appointment windows."""

    CONFIG = {
        "business_hours": {
            "monday": {"open": "09:00", "close": "17:00"},
            "tuesday": {"open": "09:00", "close": "17:00"},
        },
        "default_slot_duration_minutes": 60,
    }

    def test_single_day_segment(self):
        days = calculate_days_needed(60, datetime(2026, 1, 19), time(9, 0), self.CONFIG)
        segments = build_appointment_segments(days, time(9, 0), self.CONFIG)
        assert segments == [(datetime(2026, 1, 19, 9, 0), datetime(2026, 1, 19, 10, 0))]

    def test_multiday_segments_day1_to_close_day2_from_open(self):
        """157 min at 16:00 Monday: day 1 16:00-17:00, day 2 09:00-10:37."""
        days = calculate_days_needed(157, datetime(2026, 1, 19), time(16, 0), self.CONFIG)
        segments = build_appointment_segments(days, time(16, 0), self.CONFIG)
        assert segments == [
            (datetime(2026, 1, 19, 16, 0), datetime(2026, 1, 19, 17, 0)),
            (datetime(2026, 1, 20, 9, 0), datetime(2026, 1, 20, 10, 37)),
        ]


class TestDropElapsedSlots:
    """Tests for drop_elapsed_slots - never offer a slot that has started."""

    @staticmethod
    def _slots(*hours):
        return [
            TimeSlot(
                start=time(h, 0),
                end=time(h, 30),
                available_techs=1,
                available_tech_ids=["t1"],
            )
            for h in hours
        ]

    def test_today_drops_started_slots_keeps_future(self):
        """At noon, morning slots drop and only later ones survive."""
        date = datetime(2026, 1, 20)
        now = datetime(2026, 1, 20, 12, 0)
        kept = drop_elapsed_slots(self._slots(9, 10, 12, 13, 14), date, now)
        assert [s.start for s in kept] == [time(13, 0), time(14, 0)]

    def test_slot_starting_exactly_now_is_dropped(self):
        """A slot must start strictly in the future to remain bookable."""
        date = datetime(2026, 1, 20)
        now = datetime(2026, 1, 20, 10, 0)
        kept = drop_elapsed_slots(self._slots(10, 11), date, now)
        assert [s.start for s in kept] == [time(11, 0)]

    def test_past_date_drops_all_slots(self):
        date = datetime(2026, 1, 19)
        now = datetime(2026, 1, 20, 8, 0)
        assert drop_elapsed_slots(self._slots(9, 12, 16), date, now) == []

    def test_future_date_keeps_all_slots(self):
        date = datetime(2026, 1, 21)
        now = datetime(2026, 1, 20, 23, 0)
        slots = self._slots(9, 12, 16)
        assert drop_elapsed_slots(slots, date, now) == slots
