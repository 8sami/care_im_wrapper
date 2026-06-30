"""
Provider-agnostic inbound message normalization.

Each provider registers a normalizer function. handlers/meta.py calls
normalize_inbound() instead of doing raw extraction inline.

To add a new provider: implement _normalize_<provider>() and add it to
_NORMALIZERS. Zero other files need to change.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from care_im_wrapper.conversation.messages import InboundMessage

logger = logging.getLogger(__name__)


Normalizer = Callable[[dict[str, Any], str], "InboundMessage | None"]


def _normalize_meta(payload: dict[str, Any], channel: str) -> InboundMessage | None:
    """
    Normalizes a WhatsApp Cloud API (Meta) message payload.

    Handles:
      - type "text"        → text = payload["text"]["body"]
      - type "interactive" / "button_reply" → text = interactive["button_reply"]["id"]
      - type "interactive" / "list_reply"   → text = interactive["list_reply"]["id"]

    Returns None for stickers, images, audio, unhandled types — caller drops them.
    The `text` field carries the button/row id string for interactive replies,
    which the state machine in handlers.py disizes on directly.
    """
    raw_phone = payload.get("from")
    if not raw_phone:
        return None
    phone_number = raw_phone if raw_phone.startswith("+") else f"+{raw_phone}"

    msg_type = payload.get("type")

    if msg_type == "text":
        try:
            text = payload.get("text", {}).get("body", "").strip()
        except AttributeError:
            return None

    elif msg_type == "interactive":
        interactive = payload.get("interactive", {})
        interactive_type = interactive.get("type")
        if interactive_type == "button_reply":
            text = interactive.get("button_reply", {}).get("id", "").strip()
        elif interactive_type == "list_reply":
            text = interactive.get("list_reply", {}).get("id", "").strip()
        else:
            logger.debug("_normalize_meta: unhandled interactive subtype %r, dropping", interactive_type)
            return None

    else:
        logger.debug("_normalize_meta: unhandled message type %r, dropping", msg_type)
        return None

    if not text:
        return None

    return InboundMessage(
        phone_number=phone_number,
        text=text,
        channel=channel,
        raw_id=payload.get("id"),
    )


_NORMALIZERS: dict[str, Normalizer] = {
    "whatsapp": _normalize_meta,
    # "telegram": _normalize_telegram,
}


def normalize_inbound(payload: dict[str, Any], channel: str) -> InboundMessage | None:
    """
    Entry point for all providers. Dispatches to the channel-specific normalizer.
    Returns None if the payload cannot be normalized (caller must drop the message).
    """
    normalizer = _NORMALIZERS.get(channel)
    if normalizer is None:
        logger.error("normalize_inbound: no normalizer registered for channel %r", channel)
        return None
    return normalizer(payload, channel)
