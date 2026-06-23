from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol

from care_im_wrapper.models import ConversationSession

logger = logging.getLogger(__name__)


class MessageSender(Protocol):
    def send_text(self, to: str, body: str) -> None: ...


def _get_whatsapp_client() -> MessageSender:
    from care_im_wrapper.messaging.whatsapp import WhatsAppClient

    return WhatsAppClient()


# add more providers here

_PROVIDERS: dict[str, Callable[[], MessageSender]] = {
    ConversationSession.Provider.WHATSAPP.value: _get_whatsapp_client,
}


def send(channel: str, to: str, body: str) -> None:
    """
    Sends a text message via whichever provider `channel` identifies.
    Unknown channels are logged and dropped rather than raising errors.
    """
    factory = _PROVIDERS.get(channel)
    if factory is None:
        logger.error("messaging.send: no provider registered for channel %s", channel)
        return
    factory().send_text(to, body)
