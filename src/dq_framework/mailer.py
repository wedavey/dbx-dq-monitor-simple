"""SMTP delivery for DQ notifications. The only module that touches the network."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Optional

from .notifications import Message


def send_smtp(
    messages: list[Message],
    host: str,
    port: int,
    sender: str,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> list[str]:
    """Send every message over one STARTTLS connection. Returns error strings, one per failure.

    One bad recipient does not stop the others; the caller decides whether errors fail the job.
    """
    errors: list[str] = []
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls()
        if username:
            smtp.login(username, password or "")
        for m in messages:
            mail = EmailMessage()
            mail["From"], mail["To"], mail["Subject"] = sender, m.owner, m.subject
            mail.set_content(m.body)
            try:
                smtp.send_message(mail)
            except (smtplib.SMTPException, OSError) as exc:
                errors.append(f"{m.owner}: {exc}")
    return errors
