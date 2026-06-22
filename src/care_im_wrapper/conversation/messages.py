from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InboundMessage:
    """A normalized representation of a message received from any provider."""

    phone_number: str  # E.164, always normalized with leading "+"
    text: str
    channel: str  # "whatsapp" | "telegram" | future providers
    raw_id: str | None = None  # provider's message id (wamid, etc.)
    # status-update correlation, optional for now


@dataclass(frozen=True)
class OutboundMessage:
    """A normalized representation of a message to send via any provider."""

    text: str
    # Future: buttons, list options, media attachments, etc. as the abstraction grows.
