"""Async email client for booking notifications."""

import os
from dataclasses import dataclass
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class EmailConfig:
    """SMTP configuration loaded from environment variables."""

    host: str
    port: int
    username: str
    password: str
    use_tls: bool
    from_address: str
    notification_email: str

    @classmethod
    def from_env(cls) -> "EmailConfig | None":
        """
        Load email configuration from environment variables.

        Returns None if required variables are not set (email notifications disabled).
        """
        host = os.getenv("SMTP_HOST")
        username = os.getenv("SMTP_USER")
        password = os.getenv("SMTP_PASSWORD")
        notification_email = os.getenv("NOTIFICATION_EMAIL")

        # All four are required for email to work
        if not all([host, username, password, notification_email]):
            return None

        return cls(
            host=host,
            port=int(os.getenv("SMTP_PORT", "587")),
            username=username,
            password=password,
            use_tls=os.getenv("SMTP_USE_TLS", "true").lower() == "true",
            from_address=os.getenv("EMAIL_FROM", username),
            notification_email=notification_email,
        )


@dataclass
class BookingDetails:
    """Details needed for a booking notification email."""

    confirmation_number: str
    service_name: str
    start_time: datetime
    end_time: datetime
    technician_name: str | None
    customer_first_name: str
    customer_last_name: str
    customer_email: str | None
    customer_phone: str | None
    vehicle_year: int
    vehicle_make: str
    vehicle_model: str


class EmailClient:
    """Async email client for sending booking notifications."""

    def __init__(self, config: EmailConfig | None = None):
        """
        Initialize the email client.

        Args:
            config: Email configuration. If None, attempts to load from environment.
        """
        self.config = config or EmailConfig.from_env()
        self._enabled = self.config is not None

        if self._enabled:
            logger.info("email_client_initialized", host=self.config.host)
        else:
            logger.info("email_client_disabled", reason="SMTP configuration not set")

    @property
    def enabled(self) -> bool:
        """Return True if email notifications are enabled."""
        return self._enabled

    def _format_booking_email(self, booking: BookingDetails) -> tuple[str, str]:
        """
        Format the booking notification email.

        Returns:
            Tuple of (subject, body)
        """
        # Format date and time for subject
        date_str = booking.start_time.strftime("%A, %B %d, %Y")
        time_str = booking.start_time.strftime("%-I:%M %p")

        subject = f"Online Booking: {booking.service_name} - {date_str} at {time_str}"

        # Format time range
        start_time_str = booking.start_time.strftime("%-I:%M %p")
        end_time_str = booking.end_time.strftime("%-I:%M %p")

        # Format customer info
        customer_name = f"{booking.customer_first_name} {booking.customer_last_name}"
        customer_email = booking.customer_email or "Not provided"
        customer_phone = booking.customer_phone or "Not provided"

        # Format vehicle
        vehicle_str = f"{booking.vehicle_year} {booking.vehicle_make} {booking.vehicle_model}"

        # Format technician
        tech_str = booking.technician_name or "To be assigned"

        body = f"""================================================================================
                          NEW ONLINE BOOKING
================================================================================

Confirmation: {booking.confirmation_number}

APPOINTMENT DETAILS
-------------------
Service:     {booking.service_name}
Date:        {date_str}
Time:        {start_time_str} - {end_time_str}
Technician:  {tech_str}

CUSTOMER INFORMATION
--------------------
Name:        {customer_name}
Email:       {customer_email}
Phone:       {customer_phone}

VEHICLE INFORMATION
-------------------
Vehicle:     {vehicle_str}

================================================================================
Booked via Online Scheduling System
================================================================================
"""
        return subject, body

    async def send_booking_notification(self, booking: BookingDetails) -> bool:
        """
        Send a booking notification email.

        This method never raises exceptions - failures are logged and return False.
        This ensures email issues don't affect the booking flow.

        Args:
            booking: The booking details to include in the email.

        Returns:
            True if email was sent successfully, False otherwise.
        """
        if not self._enabled:
            logger.debug("email_skipped", reason="email not configured")
            return False

        try:
            subject, body = self._format_booking_email(booking)

            # Create message
            message = MIMEMultipart()
            message["From"] = self.config.from_address
            message["To"] = self.config.notification_email
            message["Subject"] = subject
            message.attach(MIMEText(body, "plain"))

            # Send email
            await aiosmtplib.send(
                message,
                hostname=self.config.host,
                port=self.config.port,
                username=self.config.username,
                password=self.config.password,
                start_tls=self.config.use_tls,
            )

            logger.info(
                "booking_notification_sent",
                confirmation=booking.confirmation_number,
                to=self.config.notification_email,
            )
            return True

        except aiosmtplib.SMTPException as e:
            logger.error(
                "email_smtp_error",
                confirmation=booking.confirmation_number,
                error=str(e),
            )
            return False
        except Exception as e:
            logger.error(
                "email_unexpected_error",
                confirmation=booking.confirmation_number,
                error=str(e),
                error_type=type(e).__name__,
            )
            return False


# Global instance (lazy initialized)
_email_client: EmailClient | None = None


def get_email_client() -> EmailClient:
    """Get the global email client instance."""
    global _email_client
    if _email_client is None:
        _email_client = EmailClient()
    return _email_client
