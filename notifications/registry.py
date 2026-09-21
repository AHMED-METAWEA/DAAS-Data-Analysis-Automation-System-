"""Channel construction and the credential contract each type needs.

:func:`describe_channel_types` is the single source of truth the settings UI
builds its forms from.  Without it, adding a channel means editing a Python
module and a TypeScript component and hoping the two agree about whether the
field is called ``bot_token`` or ``token``.
"""

from __future__ import annotations

from typing import Any

from db.connection_configs import decrypt_credentials
from db.monitoring_models import NotificationChannel
from notifications.base import Channel
from notifications.email_channel import EmailChannel
from notifications.inapp_channel import InAppChannel
from notifications.telegram_channel import TelegramChannel
from notifications.whatsapp_channel import WhatsAppChannel


class ChannelConfigError(ValueError):
    """The stored channel cannot be turned into a working transport."""


_BUILDERS = {
    "in_app": InAppChannel,
    "email": EmailChannel,
    "telegram": TelegramChannel,
    "whatsapp": WhatsAppChannel,
}


def build_channel(record: NotificationChannel) -> Channel:
    """Turn a stored channel row into a live transport."""
    builder = _BUILDERS.get(record.type)
    if builder is None:
        raise ChannelConfigError(f"Unknown channel type '{record.type}'.")
    credentials: dict[str, Any] = {}
    if record.encrypted_credentials:
        try:
            credentials = decrypt_credentials(record.encrypted_credentials)
        except Exception as exc:
            raise ChannelConfigError(
                f"Could not decrypt this channel's credentials: {exc}"
            ) from exc
    return builder(record.destination, credentials, record.config or {})


def channel_capabilities(channel_type: str) -> dict[str, Any]:
    """What a channel can carry — used by the composer to pick a rendering."""
    return {
        "in_app": {"markdown": True, "html": False, "max_chars": 0},
        "email": {"markdown": True, "html": True, "max_chars": 0},
        "telegram": {"markdown": False, "html": False, "max_chars": 4096},
        "whatsapp": {"markdown": False, "html": False, "max_chars": 4096},
    }.get(channel_type, {"markdown": False, "html": False, "max_chars": 0})


def describe_channel_types() -> list[dict[str, Any]]:
    """Every channel type with the fields its setup form needs.

    ``secret`` fields are encrypted at rest and never returned by the API;
    ``config`` fields are not secret and are echoed back so a user can see what
    they set.
    """
    return [
        {
            "type": "in_app",
            "label": "In-app inbox",
            "description": (
                "Alerts appear in the app's notification inbox. Always available — no setup, "
                "no credentials."
            ),
            "destination_label": "",
            "destination_placeholder": "",
            "needs_destination": False,
            "secret_fields": [],
            "config_fields": [],
        },
        {
            "type": "email",
            "label": "Email",
            "description": "Briefings by email over your own SMTP server or provider relay.",
            "destination_label": "Recipient email address",
            "destination_placeholder": "owner@company.com",
            "needs_destination": True,
            "secret_fields": [
                {"key": "username", "label": "SMTP username", "placeholder": "apikey"},
                {"key": "password", "label": "SMTP password / API key", "placeholder": ""},
            ],
            "config_fields": [
                {"key": "smtp_host", "label": "SMTP host", "placeholder": "smtp.gmail.com"},
                {"key": "smtp_port", "label": "Port", "placeholder": "587"},
                {"key": "from_address", "label": "From address", "placeholder": "daas@company.com"},
                {"key": "from_name", "label": "From name", "placeholder": "DAAS"},
            ],
        },
        {
            "type": "telegram",
            "label": "Telegram",
            "description": (
                "Fastest to set up: create a bot with @BotFather, message it once, then read "
                "your chat id from https://api.telegram.org/bot<TOKEN>/getUpdates."
            ),
            "destination_label": "Chat id",
            "destination_placeholder": "123456789",
            "needs_destination": True,
            "secret_fields": [
                {"key": "bot_token", "label": "Bot token", "placeholder": "123456:ABC-DEF..."},
            ],
            "config_fields": [],
        },
        {
            "type": "whatsapp",
            "label": "WhatsApp",
            "description": (
                "The channel most SMB owners in the region actually read. Use Meta's Cloud "
                "API for production, or Twilio's sandbox to get running in minutes. Note "
                "that scheduled briefings usually fall outside WhatsApp's 24-hour free-form "
                "window, so an approved template name is required for reliable delivery."
            ),
            "destination_label": "Recipient phone number",
            "destination_placeholder": "+20 100 123 4567",
            "needs_destination": True,
            "secret_fields": [
                {"key": "access_token", "label": "Meta access token", "placeholder": "EAAG...",
                 "provider": "meta"},
                {"key": "account_sid", "label": "Twilio Account SID", "placeholder": "AC...",
                 "provider": "twilio"},
                {"key": "auth_token", "label": "Twilio Auth Token", "placeholder": "",
                 "provider": "twilio"},
            ],
            "config_fields": [
                {"key": "provider", "label": "Provider", "placeholder": "meta",
                 "options": ["meta", "twilio"]},
                {"key": "phone_number_id", "label": "Meta phone number id",
                 "placeholder": "1234567890", "provider": "meta"},
                {"key": "template_name", "label": "Approved template name",
                 "placeholder": "daily_briefing", "provider": "meta"},
                {"key": "template_language", "label": "Template language tag",
                 "placeholder": "ar", "provider": "meta"},
                {"key": "from_number", "label": "Twilio WhatsApp sender",
                 "placeholder": "+14155238886", "provider": "twilio"},
            ],
        },
    ]
