"""WhatsApp delivery — the channel this product is actually shaped around.

In Egypt and the wider MENA SMB market, WhatsApp is not one messaging option
among several; it is where business is conducted.  An owner who will never sign
into a BI dashboard reads WhatsApp within minutes, in Arabic, on a phone.  A
three-line briefing arriving there — "revenue fell 12% yesterday, 92% of it from
one product in one region, here is the link" — is a different product from the
same analysis waiting behind a login for someone to remember it exists.

Two backends behind one channel, chosen by ``provider``:

* **meta** — the official WhatsApp Business Cloud API.  What a real deployment
  uses: free conversation tier, direct from Meta, no reseller margin.  It needs
  a verified business account and an approved template before the first message
  can be sent, which is a procurement timeline, not an afternoon.
* **twilio** — Twilio's WhatsApp sandbox works in about five minutes with a test
  number and no verification at all.

Supporting both is not indecision.  The production answer and the answer that
demonstrably works during a live demonstration are different answers, and the
gap between them is exactly the sort of thing that goes wrong on the day.

## The 24-hour window

WhatsApp does not allow arbitrary outbound messages.  Free-form text is only
permitted inside a 24-hour window opened by the *recipient* messaging you;
outside it, only a pre-approved **template** may be sent.  A scheduled 07:00
briefing is, by definition, usually outside that window.  So this channel sends
a template when one is configured and free-form text otherwise, and says plainly
which it did — a silent fallback to free-form is a message that vanishes without
an error the owner ever sees.
"""

from __future__ import annotations

import os
import re

import httpx

from notifications.base import DeliveryResult, Message

TIMEOUT_SECONDS = 25
GRAPH_VERSION = "v21.0"
# WhatsApp's own body limit for free-form text messages.
MAX_MESSAGE_CHARS = 4096
# Template variables are substituted into a single approved sentence, so each
# one has to be short. Anything longer is truncated with an ellipsis.
MAX_TEMPLATE_PARAM_CHARS = 350

_DIGITS_RE = re.compile(r"\D+")


def normalise_phone(raw: str) -> str:
    """E.164 digits only — the form both providers expect.

    A number saved as "+20 100 123 4567" and one saved as "00201001234567" are
    the same phone; sending to one and failing on the other is a support ticket
    nobody can diagnose from the error message.
    """
    digits = _DIGITS_RE.sub("", raw or "")
    if digits.startswith("00"):
        digits = digits[2:]
    return digits


class WhatsAppChannel:
    type = "whatsapp"

    def __init__(self, destination: str, credentials: dict, config: dict) -> None:
        self.destination = normalise_phone(destination)
        self.raw_destination = destination
        config = config or {}
        self.provider = (
            config.get("provider") or os.environ.get("WHATSAPP_PROVIDER") or "meta"
        ).lower()

        # Meta WhatsApp Cloud API
        self.access_token = (
            credentials.get("access_token") or os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
        )
        self.phone_number_id = (
            config.get("phone_number_id") or os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
        )
        self.template_name = (
            config.get("template_name") or os.environ.get("WHATSAPP_TEMPLATE_NAME", "")
        )
        self.template_language = (
            config.get("template_language") or os.environ.get("WHATSAPP_TEMPLATE_LANGUAGE", "")
        )

        # Twilio
        self.account_sid = (
            credentials.get("account_sid") or os.environ.get("TWILIO_ACCOUNT_SID", "")
        )
        self.auth_token = (
            credentials.get("auth_token") or os.environ.get("TWILIO_AUTH_TOKEN", "")
        )
        self.from_number = normalise_phone(
            config.get("from_number") or os.environ.get("TWILIO_WHATSAPP_FROM", "")
        )

    # ── Helpers ────────────────────────────────────────────────────────────

    def _fail(self, error: str, *, retryable: bool = False) -> DeliveryResult:
        return DeliveryResult(
            ok=False, channel_type=self.type, destination=self.destination,
            error=error, retryable=retryable,
        )

    @staticmethod
    def _retryable(status: int) -> bool:
        return status == 429 or status >= 500

    def _template_language(self, message: Message) -> str:
        """The approved template's language tag.

        An explicitly configured tag always wins: a template is approved under
        one exact tag ("ar", "en_US"), and guessing a different one produces a
        rejection whose message does not say so.
        """
        if self.template_language:
            return self.template_language
        return "ar" if message.language == "ar" else "en_US"

    # ── Meta WhatsApp Cloud API ────────────────────────────────────────────

    def _send_meta(self, message: Message) -> DeliveryResult:
        if not self.access_token:
            return self._fail(
                "No WhatsApp access token. Add a permanent token from your Meta app "
                "(or set WHATSAPP_ACCESS_TOKEN)."
            )
        if not self.phone_number_id:
            return self._fail(
                "No phone number id. Copy it from WhatsApp → API Setup in the Meta app "
                "dashboard (or set WHATSAPP_PHONE_NUMBER_ID)."
            )

        url = f"https://graph.facebook.com/{GRAPH_VERSION}/{self.phone_number_id}/messages"
        if self.template_name:
            body: dict = {
                "messaging_product": "whatsapp",
                "to": self.destination,
                "type": "template",
                "template": {
                    "name": self.template_name,
                    "language": {"code": self._template_language(message)},
                    "components": [{
                        "type": "body",
                        "parameters": [{
                            "type": "text",
                            "text": message.for_length(MAX_TEMPLATE_PARAM_CHARS),
                        }],
                    }],
                },
            }
            mode = "template"
        else:
            body = {
                "messaging_product": "whatsapp",
                "to": self.destination,
                "type": "text",
                "text": {"preview_url": False, "body": message.for_length(MAX_MESSAGE_CHARS)},
            }
            mode = "free-form"

        try:
            response = httpx.post(
                url, json=body,
                headers={"Authorization": f"Bearer {self.access_token}"},
                timeout=TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            return self._fail(f"Could not reach the WhatsApp Cloud API: {exc}", retryable=True)

        if response.status_code >= 400:
            detail = response.text[:300]
            try:
                detail = (response.json().get("error") or {}).get("message", detail)
            except ValueError:
                pass
            hint = ""
            if mode == "free-form":
                # By far the most common cause, and the one whose real error
                # message never mentions the actual rule.
                hint = (
                    " — free-form messages are only allowed within 24 hours of the "
                    "recipient's last message. Configure an approved template name on this "
                    "channel for scheduled briefings."
                )
            return self._fail(
                f"WhatsApp rejected the {mode} message ({response.status_code}): {detail}{hint}",
                retryable=self._retryable(response.status_code),
            )

        payload = response.json()
        messages = payload.get("messages") or [{}]
        return DeliveryResult(
            ok=True, channel_type=self.type, destination=self.destination,
            provider_message_id=str(messages[0].get("id") or ""),
        )

    # ── Twilio ─────────────────────────────────────────────────────────────

    def _send_twilio(self, message: Message) -> DeliveryResult:
        if not self.account_sid or not self.auth_token:
            return self._fail(
                "No Twilio credentials. Add the Account SID and Auth Token to this channel "
                "(or set TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN)."
            )
        if not self.from_number:
            return self._fail(
                "No Twilio WhatsApp sender. Use the sandbox number from the Twilio console "
                "(or set TWILIO_WHATSAPP_FROM)."
            )

        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"
        try:
            response = httpx.post(
                url,
                data={
                    "From": f"whatsapp:+{self.from_number}",
                    "To": f"whatsapp:+{self.destination}",
                    "Body": message.for_length(MAX_MESSAGE_CHARS),
                },
                auth=(self.account_sid, self.auth_token),
                timeout=TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            return self._fail(f"Could not reach Twilio: {exc}", retryable=True)

        if response.status_code >= 400:
            detail = response.text[:300]
            try:
                detail = response.json().get("message", detail)
            except ValueError:
                pass
            return self._fail(
                f"Twilio rejected the message ({response.status_code}): {detail}",
                retryable=self._retryable(response.status_code),
            )

        payload = response.json()
        return DeliveryResult(
            ok=True, channel_type=self.type, destination=self.destination,
            provider_message_id=str(payload.get("sid") or ""),
        )

    # ── Channel interface ──────────────────────────────────────────────────

    def send(self, message: Message) -> DeliveryResult:
        if not self.destination:
            return self._fail(
                f"'{self.raw_destination}' is not a usable phone number. Use the full "
                "international form, e.g. +20 100 123 4567."
            )
        if self.provider == "twilio":
            return self._send_twilio(message)
        if self.provider == "meta":
            return self._send_meta(message)
        return self._fail(
            f"Unknown WhatsApp provider '{self.provider}'. Use 'meta' or 'twilio'."
        )

    def verify(self) -> DeliveryResult:
        return self.send(Message(
            subject="DAAS test",
            text=(
                "DAAS is connected. Your scheduled business briefings will arrive here.\n"
                "داس متصل الآن. ستصلك تقارير أعمالك المجدولة هنا."
            ),
        ))
