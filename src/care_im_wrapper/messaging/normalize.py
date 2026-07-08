"""
Provider-agnostic inbound message and status-update normalization.

Each provider implements its own normalizer functions (e.g.
messaging/whatsapp.py's normalize_meta_message/normalize_meta_status) and
registers them below. handlers/meta.py and tasks.py call normalize_inbound()
/ normalize_status_update() instead of doing raw extraction inline.

To add a new provider: implement its normalizer functions in that provider's
own messaging module, then add them to _NORMALIZERS / _STATUS_NORMALIZERS.
Zero other files need to change.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from care_im_wrapper.conversation.messages import InboundMessage, StatusUpdate
from care_im_wrapper.messaging.whatsapp import normalize_meta_message, normalize_meta_status
from care_im_wrapper.models import ConversationSession

logger = logging.getLogger(__name__)


Normalizer = Callable[[dict[str, Any], str], "InboundMessage | None"]

_NORMALIZERS: dict[str, Normalizer] = {
    ConversationSession.Provider.WHATSAPP.value: normalize_meta_message,
    # "ConversationSession.Provider.TELEGRAM.value": normalize_telegram_message,
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


StatusNormalizer = Callable[[dict[str, Any], str], "StatusUpdate | None"]

_STATUS_NORMALIZERS: dict[str, StatusNormalizer] = {
    ConversationSession.Provider.WHATSAPP.value: normalize_meta_status,
    # "ConversationSession.Provider.TELEGRAM.value": normalize_telegram_status,
}


def normalize_status_update(payload: dict[str, Any], channel: str) -> StatusUpdate | None:
    """
    Entry point for all providers. Dispatches to the channel-specific status normalizer.
    Returns None if the payload cannot be normalized (caller must drop the update).
    """
    normalizer = _STATUS_NORMALIZERS.get(channel)
    if normalizer is None:
        logger.error("normalize_status_update: no normalizer registered for channel %r", channel)
        return None
    return normalizer(payload, channel)
