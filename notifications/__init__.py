"""Delivery channels for proactive briefings.

Every channel implements one interface (:class:`~notifications.base.Channel`),
so the monitoring runner never knows whether a briefing left as an email, a
Telegram message or a WhatsApp template — it hands over a
:class:`~notifications.base.Message` and records what came back.

WhatsApp is first among equals here rather than an afterthought.  In Egypt and
the wider MENA SMB market it is *the* business communication channel: a shop
owner who never opens a BI dashboard reads WhatsApp within minutes.  A daily
three-line briefing arriving there in Arabic is a different product from the
same analysis sitting behind a login.
"""

from notifications.base import Channel, DeliveryResult, Message
from notifications.registry import (
    ChannelConfigError,
    build_channel,
    channel_capabilities,
    describe_channel_types,
)

__all__ = [
    "Channel",
    "ChannelConfigError",
    "DeliveryResult",
    "Message",
    "build_channel",
    "channel_capabilities",
    "describe_channel_types",
]
