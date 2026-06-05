"""Unit tests for booking notification email formatting."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from email_client import BookingDetails, EmailClient, EmailConfig


def _client() -> EmailClient:
    return EmailClient(
        config=EmailConfig(
            host="smtp.test",
            port=587,
            username="u",
            password="p",
            use_tls=True,
            from_address="from@test",
            notification_email="shop@test",
        )
    )


def _details(start: datetime, end: datetime) -> BookingDetails:
    return BookingDetails(
        confirmation_number="SM-20260603-TEST01",
        service_name="Window Tint - Full Sedan/Truck - Ceramic",
        start_time=start,
        end_time=end,
        technician_name="Mina Vang",
        customer_first_name="Javion",
        customer_last_name="Cotton",
        customer_email="cotton@example.com",
        customer_phone="8164412152",
        vehicle_year=2026,
        vehicle_make="Honda",
        vehicle_model="Accord",
    )


class TestSingleDayEmail:
    def test_single_day_shows_one_date_and_time_range(self):
        booking = _details(datetime(2026, 6, 3, 9, 0), datetime(2026, 6, 3, 11, 37))
        _, body = _client()._format_booking_email(booking)
        assert "Wednesday, June 03, 2026" in body
        assert "9:00 AM - 11:37 AM" in body
        assert "overnight" not in body.lower()


class TestMultidayEmail:
    def test_multiday_shows_date_range_and_overnight_note(self):
        """Anne's June 3 case: 5:00 PM start, finishes the next morning."""
        booking = _details(datetime(2026, 6, 3, 17, 0), datetime(2026, 6, 4, 11, 7))
        _, body = _client()._format_booking_email(booking)
        assert "Wednesday, June 03, 2026 - Thursday, June 04, 2026" in body
        assert "5:00 PM - 11:07 AM on Thursday, June 04" in body
        assert "vehicle stays overnight" in body
