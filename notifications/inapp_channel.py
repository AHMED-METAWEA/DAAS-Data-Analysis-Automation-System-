"""The in-app inbox — the channel that always works.

Every other transport needs a credential someone has to obtain: an SMTP
password, a bot token, a verified WhatsApp business account.  Until one exists,
a monitoring system that only pushes has nothing to show for a night's work.

So delivery here is a no-op that succeeds: the alerts were already persisted by
the runner before any channel was called, and the inbox reads them from the
database.  Modelling it as a channel rather than special-casing it keeps the
runner's delivery loop uniform, and makes "in-app only" a legitimate, fully
functional configuration rather than a degraded one.
"""

from __future__ import annotations

from notifications.base import DeliveryResult, Message


class InAppChannel:
    type = "in_app"

    def __init__(self, destination: str = "", credentials: dict | None = None,
                 config: dict | None = None) -> None:
        self.destination = destination or "in-app inbox"

    def send(self, message: Message) -> DeliveryResult:
        return DeliveryResult(ok=True, channel_type=self.type, destination=self.destination)

    def verify(self) -> DeliveryResult:
        return DeliveryResult(ok=True, channel_type=self.type, destination=self.destination)
