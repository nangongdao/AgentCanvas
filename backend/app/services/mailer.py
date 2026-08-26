"""Configurable outbound email for operational notifications (C7-4).

SMTP is optional: without ``smtp_host`` the platform runs with a
``NullMailer`` that logs the payload (dev/self-hosted default — invitation
links are handed to the inviting admin in the API response instead). With
SMTP configured, the same payloads are delivered through a stdlib
``smtplib`` client executed off the event loop; no mail dependency is
added to the lockfile.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from app.core.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeliveryReport:
    delivery: str  # "email" | "manual"
    detail: str


class NullMailer:
    """Log-only fallback used when SMTP is not configured."""

    async def send(self, *, to: str, subject: str, body: str) -> DeliveryReport:
        logger.info("email suppressed (SMTP not configured) to=%s subject=%r", to, subject)
        return DeliveryReport(delivery="manual", detail="SMTP not configured")


class SmtpMailer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def send(self, *, to: str, subject: str, body: str) -> DeliveryReport:
        message = EmailMessage()
        message["From"] = self.settings.smtp_from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        await asyncio.to_thread(self._deliver, message)
        return DeliveryReport(delivery="email", detail="sent")

    def _deliver(self, message: EmailMessage) -> None:
        if self.settings.smtp_starttls:
            with smtplib.SMTP(
                self.settings.smtp_host, self.settings.smtp_port, timeout=15
            ) as client:
                client.starttls()
                if self.settings.smtp_username:
                    client.login(self.settings.smtp_username, self.settings.smtp_password)
                client.send_message(message)
            return
        with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=15) as client:
            if self.settings.smtp_username:
                client.login(self.settings.smtp_username, self.settings.smtp_password)
            client.send_message(message)


def build_mailer(settings: Settings) -> NullMailer | SmtpMailer:
    if settings.smtp_host:
        return SmtpMailer(settings)
    return NullMailer()


__all__ = ["DeliveryReport", "NullMailer", "SmtpMailer", "build_mailer"]
