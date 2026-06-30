from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class InteractiveType(str, Enum):
    REPLY_BUTTONS = "button"
    LIST = "list"
    CTA_URL = "cta_url"


@dataclass(frozen=True)
class InteractivePayload:
    """
    Provider-agnostic description of an interactive message.
    WhatsAppClient translates this into the exact Meta API JSON shape.
    Other providers translate it into their own shape, or fall back to plain text.

    action_data shapes by type:
      REPLY_BUTTONS → [{"id": str, "title": str}, ...]               max 3 items
      LIST          → [{"title": str, "rows": [{"id": str, "title": str,
                        "description": str | None}, ...]}, ...]       max 10 rows total
      CTA_URL       → [{"display_text": str, "url": str}]             exactly 1 item
    """

    type: InteractiveType
    body: str  # prompt text shown above the buttons
    action_data: list[dict[str, Any]] = field(default_factory=list)
    button_label: str = "View Options"  # list open-button label (LIST type only)
    header: str | None = None
    footer: str | None = None


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
    """
    Provider-agnostic outbound message.
    Always populate `text` — it is the plain-text fallback for non-interactive providers
    and for error recovery. Never set it to an
    Set `interactive` when an interactive UI should be shown (provider permitting).
    """

    text: str
    interactive: InteractivePayload | None = None

    def as_plain_text(self) -> str:
        return self.text
