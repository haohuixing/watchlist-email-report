"""Gmail SMTP delivery."""

from __future__ import annotations

import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import formatdate

from .config import Settings


class MailError(RuntimeError):
    """Raised when SMTP delivery fails."""


def send(settings: Settings, subject: str, text: str, html: str) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.mail_from or settings.smtp_username
    message["To"] = ", ".join(settings.mail_to)
    message["Date"] = formatdate(localtime=True)
    message.set_content(text)
    message.add_alternative(html, subtype="html")

    last_error: Exception | None = None
    for attempt in range(2):
        try:
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as smtp:
                smtp.ehlo()
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
                smtp.login(settings.smtp_username, settings.smtp_password)
                smtp.send_message(message)
            return
        except (smtplib.SMTPException, OSError) as error:
            last_error = error
            if attempt == 0:
                time.sleep(3)
    raise MailError(f"SMTP send failed: {last_error}") from last_error
