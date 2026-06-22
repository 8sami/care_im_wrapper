from __future__ import annotations

import logging
from typing import Any

from django.dispatch import receiver

from care_im_wrapper.conversation.messages import InboundMessage
from care_im_wrapper.signals import meta_message_received, meta_status_updated
from care_im_wrapper.tasks import process_inbound_message, process_status_update

logger = logging.getLogger(__name__)


@receiver(meta_message_received)
def on_meta_message(*, payload: dict[str, Any], channel: str, **kwargs: Any) -> None:
    message = _normalize_meta_message(payload, channel)
    if message is None:
        logger.warning("on_meta_message: could not normalize payload, dropping")
        return
    process_inbound_message.delay(
        phone_number=message.phone_number,
        text=message.text,
        channel=message.channel,
        raw_id=message.raw_id,
    )  # type: ignore[reportOptionalMemberAccess]


@receiver(meta_status_updated)
def on_meta_status(*, payload: dict[str, Any], channel: str, **kwargs: Any) -> None:
    process_status_update.delay(payload=payload, channel=channel)  # pyright: ignore[reportCallIssue]


def _normalize_meta_message(payload: dict[str, Any], channel: str) -> InboundMessage | None:
    """
    Translates a raw WhatsApp Cloud API message object into the
    provider-agnostic InboundMessage shape. This is the ONLY place in
    the codebase that should know WhatsApp's payload structure for
    inbound messages — tasks.py must never parse this shape again.
    """
    raw_phone = payload.get("from")
    if not raw_phone:
        return None
    phone_number = raw_phone if raw_phone.startswith("+") else f"+{raw_phone}"

    try:
        text = payload.get("text", {}).get("body", "").strip()
    except AttributeError:
        return None
    if not text:
        return None

    return InboundMessage(
        phone_number=phone_number,
        text=text,
        channel=channel,
        raw_id=payload.get("id"),
    )
