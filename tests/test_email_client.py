"""Unit tests for booking notification email formatting and delivery."""

import logging
import sys
from datetime import datetime
from pathlib import Path

import aiosmtplib
import pytest
import structlog
from structlog.testing import capture_logs

sys.path.insert(0, str(Path(__file__).parent.parent))

import email_client as email_client_module
from email_client import (
    BookingDetails,
    EmailClient,
    EmailConfig,
    get_email_client,
)

SMTP_ENV_VARS = (
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "SMTP_USE_TLS",
    "EMAIL_FROM",
    "NOTIFICATION_EMAIL",
)


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


@pytest.fixture
def clean_smtp_env(monkeypatch):
    """Remove any SMTP config inherited from the developer's shell/.env."""
    for name in SMTP_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def sent_messages(monkeypatch):
    """
    Capture calls at the SMTP boundary instead of sending real mail.

    Each entry is (message, kwargs) so tests can inspect both the composed
    MIME message and the connection parameters.
    """
    calls = []

    async def fake_send(message, **kwargs):
        calls.append((message, kwargs))

    monkeypatch.setattr(email_client_module.aiosmtplib, "send", fake_send)
    return calls


@pytest.fixture
def logged_events(monkeypatch):
    """
    Capture the structlog events email_client emits.

    The app configures structlog to filter at INFO (main.py) and to cache bound
    loggers, so a plain capture_logs() would silently drop the debug-level skip
    event depending on import order. Bind a fresh, unfiltered logger for the
    duration of the test instead.
    """
    saved_config = structlog.get_config()
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.NOTSET),
        cache_logger_on_first_use=False,
    )
    monkeypatch.setattr(
        email_client_module, "logger", structlog.get_logger("email_client_under_test")
    )
    try:
        with capture_logs() as entries:
            yield entries
    finally:
        structlog.configure(**saved_config)


def _body_of(message) -> str:
    return message.get_payload()[0].get_payload()


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


class TestEmailConfigFromEnv:
    """
    Misreading the environment either disables notifications silently or
    points mail at the wrong server, so each variable is pinned.
    """

    def test_returns_none_when_nothing_configured(self, clean_smtp_env):
        assert EmailConfig.from_env() is None

    @pytest.mark.parametrize(
        "missing",
        ["SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "NOTIFICATION_EMAIL"],
    )
    def test_returns_none_when_any_required_variable_is_missing(
        self, clean_smtp_env, monkeypatch, missing
    ):
        """A half-configured mailer must stay off rather than fail at send time."""
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_USER", "user@example.com")
        monkeypatch.setenv("SMTP_PASSWORD", "secret")
        monkeypatch.setenv("NOTIFICATION_EMAIL", "shop@example.com")
        monkeypatch.delenv(missing)

        assert EmailConfig.from_env() is None

    def test_builds_config_with_defaults_when_optional_vars_absent(
        self, clean_smtp_env, monkeypatch
    ):
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_USER", "user@example.com")
        monkeypatch.setenv("SMTP_PASSWORD", "secret")
        monkeypatch.setenv("NOTIFICATION_EMAIL", "shop@example.com")

        config = EmailConfig.from_env()

        assert config is not None
        assert config.host == "smtp.example.com"
        assert config.username == "user@example.com"
        assert config.password == "secret"
        assert config.notification_email == "shop@example.com"
        # Defaults: submission port, TLS on, sender falls back to the login.
        assert config.port == 587
        assert config.use_tls is True
        assert config.from_address == "user@example.com"

    @pytest.mark.parametrize(
        ("use_tls_env", "expected_use_tls"),
        [
            ("true", True),
            ("True", True),
            ("TRUE", True),
            ("false", False),
            ("False", False),
            ("FALSE", False),
            ("0", False),
        ],
    )
    def test_reads_optional_overrides(
        self, clean_smtp_env, monkeypatch, use_tls_env, expected_use_tls
    ):
        """
        SMTP_USE_TLS is matched case-insensitively. Only an exact, case-folded
        "true" enables STARTTLS; anything else (including "0") turns it off.
        Getting this backwards for a spelling like "TRUE" would hand the SMTP
        username and password to the server over an unencrypted connection.
        """
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_USER", "user@example.com")
        monkeypatch.setenv("SMTP_PASSWORD", "secret")
        monkeypatch.setenv("NOTIFICATION_EMAIL", "shop@example.com")
        monkeypatch.setenv("SMTP_PORT", "2525")
        monkeypatch.setenv("SMTP_USE_TLS", use_tls_env)
        monkeypatch.setenv("EMAIL_FROM", "noreply@example.com")

        config = EmailConfig.from_env()

        assert config is not None
        assert config.port == 2525
        assert config.use_tls is expected_use_tls
        assert config.from_address == "noreply@example.com"


class TestEmailClientEnablement:
    def test_client_without_config_or_env_is_disabled(self, clean_smtp_env):
        assert EmailClient().enabled is False

    def test_client_falls_back_to_environment_config(self, clean_smtp_env, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_USER", "user@example.com")
        monkeypatch.setenv("SMTP_PASSWORD", "secret")
        monkeypatch.setenv("NOTIFICATION_EMAIL", "shop@example.com")

        client = EmailClient()

        assert client.enabled is True
        assert client.config.host == "smtp.example.com"

    def test_explicit_config_is_used(self):
        client = _client()
        assert client.enabled is True
        assert client.config.notification_email == "shop@test"


class TestSendBookingNotification:
    async def test_disabled_client_reports_failure_and_never_touches_smtp(
        self, clean_smtp_env, sent_messages, logged_events
    ):
        """
        No credentials must mean no connection attempt at all, and the skip has
        to be deliberate: send_booking_notification swallows every exception
        into the same False, so returning False proves nothing on its own. The
        logs are what distinguish a clean skip from a crash on self.config.
        """
        client = EmailClient()
        booking = _details(datetime(2026, 6, 3, 9, 0), datetime(2026, 6, 3, 11, 0))

        assert await client.send_booking_notification(booking) is False
        assert sent_messages == []

        events = [entry["event"] for entry in logged_events]
        assert "email_skipped" in events
        assert "email_unexpected_error" not in events
        assert "email_smtp_error" not in events

    async def test_successful_send_returns_true_and_sends_one_message(self, sent_messages):
        booking = _details(datetime(2026, 6, 3, 9, 0), datetime(2026, 6, 3, 11, 0))

        assert await _client().send_booking_notification(booking) is True
        assert len(sent_messages) == 1

    async def test_message_is_addressed_from_sender_to_shop(self, sent_messages):
        """Swapping From/To would mail the shop's notice to the shop's own sender."""
        booking = _details(datetime(2026, 6, 3, 9, 0), datetime(2026, 6, 3, 11, 0))
        await _client().send_booking_notification(booking)

        message, _ = sent_messages[0]
        assert message["From"] == "from@test"
        assert message["To"] == "shop@test"

    async def test_subject_identifies_service_date_and_time(self, sent_messages):
        booking = _details(datetime(2026, 6, 3, 9, 0), datetime(2026, 6, 3, 11, 0))
        await _client().send_booking_notification(booking)

        message, _ = sent_messages[0]
        subject = message["Subject"]
        assert "Window Tint - Full Sedan/Truck - Ceramic" in subject
        assert "Wednesday, June 03, 2026" in subject
        assert "9:00 AM" in subject

    async def test_body_carries_every_detail_staff_need_to_find_the_booking(self, sent_messages):
        """
        The shop works this email by hand; a dropped field means an
        appointment nobody can match to a customer or a car.
        """
        booking = _details(datetime(2026, 6, 3, 9, 0), datetime(2026, 6, 3, 11, 30))
        await _client().send_booking_notification(booking)

        message, _ = sent_messages[0]
        body = _body_of(message)
        assert "SM-20260603-TEST01" in body
        assert "Window Tint - Full Sedan/Truck - Ceramic" in body
        assert "9:00 AM - 11:30 AM" in body
        assert "Mina Vang" in body
        assert "Javion Cotton" in body
        assert "cotton@example.com" in body
        assert "8164412152" in body
        assert "2026 Honda Accord" in body

    async def test_missing_contact_and_technician_are_labelled_not_blank(self, sent_messages):
        """Blank lines read as a formatting bug; explicit placeholders don't."""
        booking = _details(datetime(2026, 6, 3, 9, 0), datetime(2026, 6, 3, 11, 0))
        booking.customer_email = None
        booking.customer_phone = None
        booking.technician_name = None

        await _client().send_booking_notification(booking)

        body = _body_of(sent_messages[0][0])
        assert "Email:       Not provided" in body
        assert "Phone:       Not provided" in body
        assert "Technician:  To be assigned" in body

    async def test_send_uses_the_configured_server_and_credentials(self, sent_messages):
        """Wrong host/port/TLS silently sends nothing or leaks credentials."""
        booking = _details(datetime(2026, 6, 3, 9, 0), datetime(2026, 6, 3, 11, 0))
        await _client().send_booking_notification(booking)

        _, kwargs = sent_messages[0]
        assert kwargs["hostname"] == "smtp.test"
        assert kwargs["port"] == 587
        assert kwargs["username"] == "u"
        assert kwargs["password"] == "p"
        assert kwargs["start_tls"] is True

    async def test_tls_disabled_config_is_honoured(self, sent_messages):
        client = EmailClient(
            config=EmailConfig(
                host="smtp.test",
                port=25,
                username="u",
                password="p",
                use_tls=False,
                from_address="from@test",
                notification_email="shop@test",
            )
        )
        booking = _details(datetime(2026, 6, 3, 9, 0), datetime(2026, 6, 3, 11, 0))

        await client.send_booking_notification(booking)

        _, kwargs = sent_messages[0]
        assert kwargs["start_tls"] is False
        assert kwargs["port"] == 25

    async def test_smtp_failure_returns_false_without_raising(self, monkeypatch):
        """A dead mail server must never break a confirmed booking."""

        async def boom(message, **kwargs):
            raise aiosmtplib.SMTPException("mailbox unavailable")

        monkeypatch.setattr(email_client_module.aiosmtplib, "send", boom)
        booking = _details(datetime(2026, 6, 3, 9, 0), datetime(2026, 6, 3, 11, 0))

        assert await _client().send_booking_notification(booking) is False

    async def test_connection_error_returns_false_without_raising(self, monkeypatch):
        """Non-SMTP failures (DNS, TCP, timeouts) must be swallowed too."""

        async def boom(message, **kwargs):
            raise ConnectionRefusedError("no route to smtp.test")

        monkeypatch.setattr(email_client_module.aiosmtplib, "send", boom)
        booking = _details(datetime(2026, 6, 3, 9, 0), datetime(2026, 6, 3, 11, 0))

        assert await _client().send_booking_notification(booking) is False

    async def test_formatting_failure_returns_false_without_raising(self, sent_messages):
        """
        A booking whose start_time is None blows up in strftime during
        formatting; that must be swallowed rather than escaping as an exception
        into the booking endpoint. (Naive datetimes are fine - every other test
        here uses them.)
        """
        booking = _details(datetime(2026, 6, 3, 9, 0), datetime(2026, 6, 3, 11, 0))
        booking.start_time = None

        assert await _client().send_booking_notification(booking) is False
        assert sent_messages == []


class TestGetEmailClient:
    def test_returns_a_cached_singleton(self, clean_smtp_env, monkeypatch):
        """Re-initialising per booking would re-read config on every request."""
        monkeypatch.setattr(email_client_module, "_email_client", None)

        first = get_email_client()
        second = get_email_client()

        assert first is second


class TestSmtpPortIsNeverFatal:
    """Regression: a bad SMTP_PORT used to raise ValueError out of from_env().

    get_email_client() runs AFTER the appointment has been created, so the
    exception surfaced as a 500 on a booking that actually succeeded - the
    customer then retried and double-booked a slot they already held. Worse,
    .env.example ships an SMTP_PORT line, so present-but-empty (which
    os.getenv's default does NOT cover) is the normal state of a copied .env.
    """

    def _configure(self, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_USER", "user@example.com")
        monkeypatch.setenv("SMTP_PASSWORD", "secret")
        monkeypatch.setenv("NOTIFICATION_EMAIL", "shop@example.com")

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_blank_port_falls_back_to_the_default(self, clean_smtp_env, monkeypatch, blank):
        """Present-but-empty means "unset", not "broken" - mail stays enabled."""
        self._configure(monkeypatch)
        monkeypatch.setenv("SMTP_PORT", blank)

        config = EmailConfig.from_env()

        assert config is not None, "a blank port must not disable notifications"
        assert config.port == 587

    @pytest.mark.parametrize("bad", ["abc", "58 7", "5.87"])
    def test_malformed_port_disables_email_instead_of_raising(
        self, clean_smtp_env, monkeypatch, bad
    ):
        """A genuinely malformed port turns notifications off. It must NOT
        raise: booking must never fail because of the mailer."""
        self._configure(monkeypatch)
        monkeypatch.setenv("SMTP_PORT", bad)

        config = EmailConfig.from_env()  # must not raise

        assert config is None

    def test_valid_port_is_still_honoured(self, clean_smtp_env, monkeypatch):
        """The guard must not swallow a correctly configured port."""
        self._configure(monkeypatch)
        monkeypatch.setenv("SMTP_PORT", "2525")

        config = EmailConfig.from_env()

        assert config is not None
        assert config.port == 2525
