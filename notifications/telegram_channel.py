"""Telegram delivery via the Bot API.

The cheapest channel to get working end to end: create a bot with @BotFather,
send it one message, read the chat id, done — no business verification, no
approval queue, no per-message cost.  That makes it the right default for a
demo and a genuinely good option for a small team.
"""

from __future__ import annotations

import os

import httpx

from notifications.base import DeliveryResult, Message

API_BASE = "https://api.telegram.org"
TIMEOUT_SECONDS = 20
# Telegram rejects anything longer outright rather than truncating it.
MAX_MESSAGE_CHARS = 4096


class TelegramChannel:
    type = "telegram"

    def __init__(self, destination: str, credentials: dict, config: dict) -> None:
        # destination is the chat id (a user, a group, or a channel @handle).
        self.destination = destination
        self.token = credentials.get("bot_token") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.config = config or {}

    def _fail(self, error: str, *, retryable: bool = False) -> DeliveryResult:
        return DeliveryResult(
            ok=False, channel_type=self.type, destination=self.destination,
            error=error, retryable=retryable,
        )

    def send(self, message: Message) -> DeliveryResult:
        if not self.token:
            return self._fail(
                "No Telegram bot token. Create a bot with @BotFather and save its token on "
                "this channel (or set TELEGRAM_BOT_TOKEN)."
            )
        if not self.destination:
            return self._fail(
                "No chat id. Send your bot any message, then open "
                "https://api.telegram.org/bot<TOKEN>/getUpdates to read it."
            )

        body = message.for_length(MAX_MESSAGE_CHARS)
        try:
            response = httpx.post(
                f"{API_BASE}/bot{self.token}/sendMessage",
                json={
                    "chat_id": self.destination,
                    "text": body,
                    "disable_web_page_preview": True,
                },
                timeout=TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            return self._fail(f"Could not reach Telegram: {exc}", retryable=True)

        if response.status_code >= 400:
            detail = ""
            try:
                detail = response.json().get("description", "")
            except ValueError:
                detail = response.text[:200]
            return self._fail(
                f"Telegram rejected the message ({response.status_code}): {detail}",
                # 429 is rate limiting and 5xx is Telegram's problem; both pass
                # on a later run. A 400 means the chat id or token is wrong and
                # will keep meaning that.
                retryable=response.status_code == 429 or response.status_code >= 500,
            )

        payload = response.json()
        return DeliveryResult(
            ok=True, channel_type=self.type, destination=self.destination,
            provider_message_id=str((payload.get("result") or {}).get("message_id") or ""),
        )

    def verify(self) -> DeliveryResult:
        return self.send(Message(
            subject="DAAS test",
            text="DAAS is connected. Scheduled briefings will arrive here.",
        ))
