from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

from care_im_wrapper.conversation.messages import OutboundMessage
from care_im_wrapper.conversation.template_rendering import merge_variable_values
from care_im_wrapper.models import ConversationSession
from care_im_wrapper.settings import plugin_settings

if TYPE_CHECKING:
    from care_im_wrapper.models.notification import NotificationTemplate

logger = logging.getLogger(__name__)


class MessageSender(Protocol):
    supports_interactive: bool
    supports_templates: bool
    max_message_chars: int
    min_send_interval_seconds: int

    def send_text(self, to: str, body: str) -> str | None: ...
    def send_interactive(self, to: str, msg: OutboundMessage) -> str | None: ...
    def send_template(self, to: str, template: NotificationTemplate, merged_variables: dict) -> str | None: ...
    def sync_templates(self) -> None: ...


def _get_whatsapp_client() -> MessageSender:
    from care_im_wrapper.messaging.whatsapp import WhatsAppClient

    return WhatsAppClient()


# add more providers here

_PROVIDERS: dict[str, Callable[[], MessageSender]] = {
    ConversationSession.Provider.WHATSAPP.value: _get_whatsapp_client,
}


def get_max_chars(channel: str) -> int:
    """Returns the maximum allowed characters for a given provider."""
    factory = _PROVIDERS.get(channel)
    if factory is None:
        return 4096  # Default fallback
    return factory().max_message_chars


def get_min_send_interval_seconds(channel: str) -> int:
    """Returns the minimum seconds between sends to the same number for a given provider."""
    factory = _PROVIDERS.get(channel)
    if factory is None:
        return 0  # Unregistered channel: no real provider to throttle against
    return factory().min_send_interval_seconds


def get_template_capable_providers() -> list[tuple[str, MessageSender]]:
    """Returns (channel, client) pairs for every registered provider that supports templates."""
    return [
        (channel, client)
        for channel, client in ((channel, factory()) for channel, factory in _PROVIDERS.items())
        if client.supports_templates
    ]


def get_default_channel() -> str:
    """Last-resort channel fallback, configured via `NOTIFICATION_DEFAULT_PROVIDER`."""
    return plugin_settings.NOTIFICATION_DEFAULT_PROVIDER


def resolve_channel(phone_number: str) -> str:
    """Channel to notify a number on: its most recent `ConversationSession` provider,
    else `get_default_channel()`."""
    session = ConversationSession.objects.filter(phone_number=phone_number).order_by("-updated_at").first()  # pyright: ignore[reportAttributeAccessIssue]
    if session is not None:
        return session.provider
    return get_default_channel()


def send_message(channel: str, to: str, msg: OutboundMessage | str) -> str | None:
    if isinstance(msg, str):
        msg = OutboundMessage(text=msg)

    """
    Single outbound entry point for all new code — use this for menus and navigation.
    Capability-based dispatch: if the provider supports interactive AND msg.interactive
    is set, calls send_interactive(). On any failure, falls back to send_text().
    For non-interactive providers (or msg.interactive=None), always uses send_text().
    """
    factory = _PROVIDERS.get(channel)
    if factory is None:
        logger.error("messaging.send_message: no provider registered for channel %s", channel)
        return None
    client = factory()
    if client.supports_interactive and msg.interactive is not None:
        try:
            return client.send_interactive(to, msg)
        except Exception:
            logger.warning(
                "send_message: interactive send failed for channel %s, falling back to plain text",
                channel,
                exc_info=True,
            )
    return client.send_text(to, msg.as_plain_text())


def send_template_message(
    channel: str,
    to: str,
    template: NotificationTemplate,
    event_variable_values: dict | None,
    recipient_variable_overrides: dict | None,
) -> str | None:
    factory = _PROVIDERS.get(channel)
    if factory is None:
        logger.error("messaging.send_template_message: no provider registered for channel %s", channel)
        return None
    client = factory()
    if not client.supports_templates:
        logger.error("messaging.send_template_message: provider %s does not support templates", channel)
        return None
    merged = merge_variable_values(event_variable_values, recipient_variable_overrides)
    return client.send_template(to, template, merged)
