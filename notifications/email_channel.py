"""Email delivery over SMTP.

No third-party email SDK: SMTP is in the standard library, works against Gmail,
Outlook, Mailgun, SendGrid and a company's own relay without changing a line,
and adds nothing to the dependency list.  Credentials come from the channel's
encrypted record, falling back to process env vars so a single-tenant
deployment can configure it once.
"""

from __future__ import annotations

import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from notifications.base import DeliveryResult, Message

DEFAULT_TIMEOUT_SECONDS = 20
_ADDRESS_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# SMTP failures that a later run could plausibly succeed at. Anything else
# (authentication, a rejected recipient) will fail identically forever, and
# retrying it just burns the schedule's time budget.
_RETRYABLE_SMTP_CODES = {421, 450, 451, 452, 454, 471}


class EmailChannel:
    type = "email"

    def __init__(self, destination: str, credentials: dict, config: dict) -> None:
        self.destination = destination
        self.host = config.get("smtp_host") or os.environ.get("SMTP_HOST", "")
        self.port = int(config.get("smtp_port") or os.environ.get("SMTP_PORT", 587))
        self.username = credentials.get("username") or os.environ.get("SMTP_USERNAME", "")
        self.password = credentials.get("password") or os.environ.get("SMTP_PASSWORD", "")
        self.from_address = (
            config.get("from_address") or os.environ.get("SMTP_FROM") or self.username
        )
        self.from_name = config.get("from_name") or os.environ.get("SMTP_FROM_NAME", "DAAS")
        # STARTTLS on 587, implicit TLS on 465 — the two conventions in the wild.
        self.use_ssl = bool(config.get("use_ssl", self.port == 465))

    def _fail(self, error: str, *, retryable: bool = False) -> DeliveryResult:
        return DeliveryResult(
            ok=False, channel_type=self.type, destination=self.destination,
            error=error, retryable=retryable,
        )

    def _build(self, message: Message) -> EmailMessage:
        mail = EmailMessage()
        mail["Subject"] = message.subject
        mail["From"] = formataddr((self.from_name, self.from_address))
        mail["To"] = self.destination
        mail.set_content(message.text)
        if message.html:
            mail.add_alternative(message.html, subtype="html")
        return mail

    def send(self, message: Message) -> DeliveryResult:
        if not self.host:
            return self._fail(
                "No SMTP host configured. Set it on the channel, or set SMTP_HOST in the "
                "environment."
            )
        if not _ADDRESS_RE.match(self.destination or ""):
            return self._fail(f"'{self.destination}' is not a valid email address.")
        if not self.from_address:
            return self._fail(
                "No From address. Set `from_address` on the channel or SMTP_FROM in the "
                "environment — most relays reject a message without one."
            )

        mail = self._build(message)
        try:
            if self.use_ssl:
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(
                    self.host, self.port, timeout=DEFAULT_TIMEOUT_SECONDS, context=context,
                ) as server:
                    if self.username:
                        server.login(self.username, self.password)
                    server.send_message(mail)
            else:
                with smtplib.SMTP(self.host, self.port, timeout=DEFAULT_TIMEOUT_SECONDS) as server:
                    server.ehlo()
                    try:
                        server.starttls(context=ssl.create_default_context())
                        server.ehlo()
                    except smtplib.SMTPNotSupportedError:
                        # A local relay with no TLS. Allowed, but it is worth
                        # knowing it happened rather than assuming encryption.
                        pass
                    if self.username:
                        server.login(self.username, self.password)
                    server.send_message(mail)
        except smtplib.SMTPAuthenticationError as exc:
            return self._fail(f"SMTP authentication failed: {exc}")
        except smtplib.SMTPResponseException as exc:
            return self._fail(
                f"SMTP error {exc.smtp_code}: {exc.smtp_error}",
                retryable=exc.smtp_code in _RETRYABLE_SMTP_CODES,
            )
        except (OSError, smtplib.SMTPException) as exc:
            return self._fail(f"Could not reach the SMTP server: {exc}", retryable=True)

        return DeliveryResult(ok=True, channel_type=self.type, destination=self.destination)

    def verify(self) -> DeliveryResult:
        return self.send(Message(
            subject="DAAS — notification channel test",
            text=(
                "This is a test from DAAS.\n\n"
                "If you are reading this, scheduled briefings will reach this address."
            ),
            html=(
                "<p>This is a test from <strong>DAAS</strong>.</p>"
                "<p>If you are reading this, scheduled briefings will reach this address.</p>"
            ),
        ))
