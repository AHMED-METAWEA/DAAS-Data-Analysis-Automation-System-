"""The channel contract.

One interface, four transports.  The runner composes a briefing once and each
channel renders it to whatever its medium can carry — WhatsApp has no markdown
tables, email has no 4096-character limit, the in-app inbox has neither problem.
Putting that knowledge inside the channel is what keeps the composer free of
``if channel == "whatsapp"`` branches.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class Message:
    """One briefing, in every form a channel might need.

    Channels pick the representation they can carry.  ``text`` is the lowest
    common denominator and is always populated — a channel is never left
    guessing how to degrade.
    """

    subject: str
    text: str                       # plain text, no markup
    markdown: str = ""              # full markdown, for email/in-app
    html: str = ""                  # rendered HTML, for email
    language: str = "en"
    # Where a reader goes to see the whole thing.
    link: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def for_length(self, limit: int) -> str:
        """``text`` truncated on a sentence or line boundary, never mid-word.

        A briefing cut at exactly 4096 characters ends inside a number, which is
        the one place a truncation can change what the message says.
        """
        if limit <= 0 or len(self.text) <= limit:
            return self.text
        head = self.text[: limit - 1]
        for boundary in ("\n\n", "\n", ". ", " "):
            cut = head.rfind(boundary)
            if cut > limit * 0.6:
                head = head[:cut]
                break
        return head.rstrip() + "…"


@dataclass
class DeliveryResult:
    """What happened when a message was handed to a transport."""

    ok: bool
    channel_type: str
    destination: str = ""
    provider_message_id: str | None = None
    error: str | None = None
    attempts: int = 1
    # True when the failure is worth retrying (timeout, 5xx, rate limit) as
    # opposed to permanent (bad token, malformed number). Retrying a bad token
    # forever is how a scheduler ends up rate-limited on every run.
    retryable: bool = False

    @property
    def status(self) -> str:
        return "sent" if self.ok else "failed"


class Channel(Protocol):
    """A transport that can deliver a :class:`Message`."""

    type: str
    destination: str

    def send(self, message: Message) -> DeliveryResult:
        ...

    def verify(self) -> DeliveryResult:
        """Send a test message. The only honest proof a channel works."""
        ...


_MD_PATTERNS = (
    (re.compile(r"^#{1,6}\s*", re.MULTILINE), ""),        # headings
    (re.compile(r"\*\*(.+?)\*\*", re.DOTALL), r"\1"),     # bold
    (re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", re.DOTALL), r"\1"),  # italics
    (re.compile(r"`{1,3}([^`]*)`{1,3}", re.DOTALL), r"\1"),
    (re.compile(r"^\s*[-*+]\s+", re.MULTILINE), "• "),
    (re.compile(r"\[(.+?)\]\((.+?)\)"), r"\1 (\2)"),      # links
    (re.compile(r"\n{3,}"), "\n\n"),
)


def markdown_to_text(markdown: str) -> str:
    """Flatten markdown for transports that render none of it.

    Deliberately lossy and deliberately simple: the briefings this sends are
    short prose with bullets, not documents, and a full markdown parser here
    would be a dependency bought to solve a problem nobody has.
    """
    text = markdown or ""
    for pattern, replacement in _MD_PATTERNS:
        text = pattern.sub(replacement, text)
    return text.strip()
