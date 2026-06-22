from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol

logger = logging.getLogger(__name__)


class MessageSender(Protocol):
    def send_text(self, to: str, body: str) -> None: ...


def _get_whatsapp_client() -> MessageSender:
    from care_im_wrapper.messaging.whatsapp import WhatsAppClient

    return WhatsAppClient()


_PROVIDERS: dict[str, Callable[[], MessageSender]] = {
    "whatsapp": _get_whatsapp_client,
}


def send(channel: str, to: str, body: str) -> None:
    """
    Sends a text message via whichever provider `channel` identifies.
    Unknown channels are logged and dropped rather than raising —
    a malformed or future-unsupported channel value must never crash
    the Celery task that's trying to reply to a user.
    """
    factory = _PROVIDERS.get(channel)
    if factory is None:
        logger.error("messaging.send: no provider registered for channel %s", channel)
        return
    factory().send_text(to, body)
