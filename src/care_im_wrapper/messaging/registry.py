from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

from care_im_wrapper.conversation.messages import OutboundMessage, SentTemplate
from care_im_wrapper.conversation.template_rendering import merge_variable_values
from care_im_wrapper.core.rate_limit import is_outbound_rate_limited
from care_im_wrapper.core.sanitize import mask_phone_number
from care_im_wrapper.messaging.exceptions import OutboundRateLimitedError, TransientSendError
from care_im_wrapper.messaging.limits import ChannelLimits, default_limits
from care_im_wrapper.models import ConversationSession
from care_im_wrapper.settings import plugin_settings

if TYPE_CHECKING:
    from care_im_wrapper.models.notification import NotificationTemplate

logger = logging.getLogger(__name__)


class MessageSender(Protocol):
    supports_interactive: bool
    supports_templates: bool

    # Read-only, so an implementation is free to back these with a property and read its
    # settings at call time rather than freezing them at import (see WhatsAppClient).
    @property
    def max_message_chars(self) -> int: ...
    @property
    def min_send_interval_seconds(self) -> int: ...
    @property
    def limits(self) -> ChannelLimits:
        """Every field cap this provider imposes, in one object."""
        ...

    def send_text(self, to: str, body: str) -> str | None: ...
    def send_interactive(self, to: str, msg: OutboundMessage) -> str | None: ...
    def send_template(
        self, to: str, template: NotificationTemplate, related_object: Any, context: dict
    ) -> SentTemplate: ...
    def sync_templates(self) -> None: ...
    def validate_variable_mapping_value(self, expr: str) -> list[str]:
        """Provider-specific formatting rules for one variable_mapping expression.
        Returns human-readable problems ([] = valid); see WhatsAppClient for an example."""
        ...

    def declared_placeholders(self, template: NotificationTemplate) -> list[str]:
        """Every placeholder this template's approved body requires a value for."""
        ...


def _get_whatsapp_client() -> MessageSender:
    from care_im_wrapper.messaging.whatsapp import WhatsAppClient

    return WhatsAppClient()


# add more providers here

_PROVIDERS: dict[str, Callable[[], MessageSender]] = {
    ConversationSession.Provider.WHATSAPP.value: _get_whatsapp_client,
}


def get_channel_limits(channel: str) -> ChannelLimits:
    """Every cap the channel imposes, so a caller composing a message never has to name a
    provider. A registered provider describes itself; anything else gets the generic
    defaults, which are deliberately the most restrictive reading of them."""
    factory = _PROVIDERS.get(channel)
    if factory is None:
        return default_limits()
    return factory().limits


def get_min_send_interval_seconds(channel: str) -> int:
    """Returns the minimum seconds between sends to the same number for a given provider."""
    factory = _PROVIDERS.get(channel)
    if factory is None:
        return int(plugin_settings.DEFAULT_MIN_SEND_INTERVAL_SECONDS)
    return factory().min_send_interval_seconds


def get_template_capable_providers() -> list[tuple[str, MessageSender]]:
    """Returns (channel, client) pairs for every registered provider that supports templates."""
    return [
        (channel, client)
        for channel, client in ((channel, factory()) for channel, factory in _PROVIDERS.items())
        if client.supports_templates
    ]


def validate_provider_expression(channel: str, expr: str) -> list[str]:
    """Provider-specific formatting problems for one expression ([] = valid, or
    the provider is unregistered). Dispatches through _PROVIDERS like other capability lookups."""
    factory = _PROVIDERS.get(channel)
    if factory is None:
        return []
    return factory().validate_variable_mapping_value(expr)


def get_declared_placeholders(channel: str, template: NotificationTemplate) -> list[str]:
    """Placeholders the channel's approved template body requires values for ([] when the
    provider is unregistered, i.e. nothing can be asserted about its shape)."""
    factory = _PROVIDERS.get(channel)
    if factory is None:
        return []
    return factory().declared_placeholders(template)


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


def send_message(channel: str, to: str, msg: OutboundMessage | str, *, pace: bool = True) -> str | None:
    """
    Single outbound entry point for all new code — use this for menus and navigation.
    Capability-based dispatch: if the provider supports interactive AND msg.interactive
    is set, calls send_interactive(). On any failure, falls back to send_text().
    For non-interactive providers (or msg.interactive=None), always uses send_text().
    Raises OutboundRateLimitedError if the provider's minimum per-recipient send
    interval hasn't elapsed yet -- callers running as a Celery task should catch this
    and retry after get_min_send_interval_seconds(channel) rather than attempt the send.

    Pass ``pace=False`` for the 2nd+ message of a single turn, where the provider's shape
    forced one reply to be split. Throttling mid-turn aborts the turn after earlier messages
    are already delivered, the caller's transaction rolls the session back as though they
    weren't, and the retry replays every send.
    """
    if isinstance(msg, str):
        msg = OutboundMessage(text=msg)

    factory = _PROVIDERS.get(channel)
    if factory is None:
        logger.error("messaging.send_message: no provider registered for channel %s", channel)
        return None
    if pace and is_outbound_rate_limited(channel, to):
        raise OutboundRateLimitedError(
            f"Outbound send to {mask_phone_number(to)} on channel {channel} is rate-limited."
        )
    client = factory()
    if client.supports_interactive and msg.interactive is not None:
        try:
            return client.send_interactive(to, msg)
        except TransientSendError:
            # The provider could not take the message *now* -- a rate limit, a timeout, a
            # 5xx. Re-sending the same content as plain text is a second request it has
            # just refused, which against a per-recipient rate limit doubles the rate we
            # are being punished for. Let it propagate and be retried whole.
            raise
        except Exception:
            # Anything else is a problem with the interactive payload itself, which plain
            # text does not have.
            logger.warning(
                "send_message: interactive send rejected for channel %s, falling back to plain text",
                channel,
                exc_info=True,
            )
    return client.send_text(to, msg.as_plain_text())


def send_template_message(
    channel: str,
    to: str,
    template: NotificationTemplate,
    related_object: Any,
    event_variable_values: dict | None,
    recipient_variable_overrides: dict | None,
) -> SentTemplate:
    factory = _PROVIDERS.get(channel)
    if factory is None:
        logger.error("messaging.send_template_message: no provider registered for channel %s", channel)
        return SentTemplate(tracking_id=None)
    client = factory()
    if not client.supports_templates:
        logger.error("messaging.send_template_message: provider %s does not support templates", channel)
        return SentTemplate(tracking_id=None)
    context = merge_variable_values(event_variable_values, recipient_variable_overrides)
    return client.send_template(to, template, related_object, context)
