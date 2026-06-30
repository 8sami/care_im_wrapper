from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol

from care_im_wrapper.conversation.messages import OutboundMessage
from care_im_wrapper.models import ConversationSession

logger = logging.getLogger(__name__)


class MessageSender(Protocol):
    supports_interactive: bool

    def send_text(self, to: str, body: str) -> None: ...
    def send_interactive(self, to: str, msg: OutboundMessage) -> None: ...


def _get_whatsapp_client() -> MessageSender:
    from care_im_wrapper.messaging.whatsapp import WhatsAppClient

    return WhatsAppClient()


# add more providers here

_PROVIDERS: dict[str, Callable[[], MessageSender]] = {
    ConversationSession.Provider.WHATSAPP.value: _get_whatsapp_client,
}


def send(channel: str, to: str, body: str) -> None:
    """
    Sends a plain-text message. DO NOT REMOVE — still used by handlers.py
    as messaging_send() for all data-fetch responses.
    """
    factory = _PROVIDERS.get(channel)
    if factory is None:
        logger.error("messaging.send: no provider registered for channel %s", channel)
        return
    factory().send_text(to, body)


def send_message(channel: str, to: str, msg: OutboundMessage) -> None:
    """
    Single outbound entry point for all new code — use this for menus and navigation.
    Capability-based dispatch: if the provider supports interactive AND msg.interactive
    is set, calls send_interactive(). On any failure, falls back to send_text().
    For non-interactive providers (or msg.interactive=None), always uses send_text().
    """
    factory = _PROVIDERS.get(channel)
    if factory is None:
        logger.error("messaging.send_message: no provider registered for channel %s", channel)
        return
    client = factory()
    if client.supports_interactive and msg.interactive is not None:
        try:
            client.send_interactive(to, msg)
            return
        except Exception:
            logger.warning(
                "send_message: interactive send failed for channel %s, falling back to plain text",
                channel,
                exc_info=True,
            )
    client.send_text(to, msg.as_plain_text())
