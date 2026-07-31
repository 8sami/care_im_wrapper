"""Estimated-waiting-time notification, sent when a queue token is issued to a patient."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from care.emr.models.scheduling.token import Token  # pyright: ignore[reportMissingImports]
from care.emr.resources.scheduling.token.spec import TokenStatusOptions  # pyright: ignore[reportMissingImports]
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from care_im_wrapper.handlers.dispatch import NotificationRecipientSpec, fire_notification_event
from care_im_wrapper.models.notification import _FACILITY_RESOLVERS
from care_im_wrapper.reports.context_builders import NOTIFICATION_CONTEXT_REGISTRY, TokenContext
from care_im_wrapper.settings import plugin_settings

logger = logging.getLogger(__name__)

# Set on the wait_time_update trigger's context_slug.
TOKEN_CONTEXT_SLUG = "token"

_PENDING_TOKEN_STATUSES = (
    TokenStatusOptions.CREATED.value,
    TokenStatusOptions.IN_PROGRESS.value,
    TokenStatusOptions.UNFULFILLED.value,
)


def _resolve_token_facility(token: Token) -> Any | None:
    return token.facility


_FACILITY_RESOLVERS[Token] = _resolve_token_facility
NOTIFICATION_CONTEXT_REGISTRY.register(TOKEN_CONTEXT_SLUG, TokenContext)


def count_tokens_ahead(token: Token) -> int:
    """Tokens in the same queue, still pending, with a lower number than this one."""
    return Token.objects.filter(
        queue_id=token.queue_id,
        status__in=_PENDING_TOKEN_STATUSES,
        number__lt=token.number,
    ).count()


def _plural(value: int, unit: str) -> str:
    return f"{value} {unit}{'s' if value != 1 else ''}"


def humanize_wait(minutes: int) -> str:
    """Largest two units only -- "3 days 4 hours", never "3 days 4 hours 18 minutes"."""
    if minutes <= 0:
        return "under a minute"
    if minutes < 60:
        return _plural(minutes, "minute")

    hours, remaining_minutes = divmod(minutes, 60)
    if hours < 24:
        if remaining_minutes == 0:
            return _plural(hours, "hour")
        return f"{_plural(hours, 'hour')} {_plural(remaining_minutes, 'minute')}"

    days, remaining_hours = divmod(hours, 24)
    if remaining_hours == 0:
        return _plural(days, "day")
    return f"{_plural(days, 'day')} {_plural(remaining_hours, 'hour')}"


def scheduled_start(token: Token) -> datetime | None:
    """When the token's booking is due to start, or None for a walk-in token."""
    booking = token.booking
    slot = getattr(booking, "token_slot", None) if booking else None
    return getattr(slot, "start_datetime", None)


def estimate_wait(token: Token) -> str:
    start = scheduled_start(token)
    if start is not None:
        remaining = (start - timezone.now()).total_seconds()
        if remaining > 0:
            return humanize_wait(int((remaining + 30) // 60))

    minutes_per_token = int(plugin_settings.WAIT_TIME_MINUTES_PER_TOKEN)
    return humanize_wait(count_tokens_ahead(token) * minutes_per_token)


def describe_service(token: Token) -> str:
    """What the token is for, read off the queue's schedulable resource."""
    resource = getattr(token.queue, "resource", None)
    if resource is None:
        return token.queue.name

    resource_type = getattr(resource, "resource_type", None)
    if resource_type == "location":
        location = getattr(resource, "location", None)
        name = getattr(location, "name", None) if location else None
        return name or token.queue.name
    if resource_type == "healthcare_service":
        healthcare_service = getattr(resource, "healthcare_service", None)
        name = getattr(healthcare_service, "name", None) if healthcare_service else None
        return name or token.queue.name

    user = getattr(resource, "user", None)
    full_name = getattr(user, "full_name", None) if user else None
    return full_name or token.queue.name


@receiver(post_save, sender=Token)
def on_token_post_save(sender: type[Token], instance: Token, created: bool, **kwargs: Any) -> None:
    """Sends the estimate once, when the token is issued."""
    if not created:
        return

    patient = instance.patient
    if patient is None:
        # Tokens can be issued without a patient (walk-in placeholders); nobody to notify.
        return

    event = f"token #{instance.number}"
    fire_notification_event(
        trigger_slug=plugin_settings.WAIT_TIME_TRIGGER_SLUG,
        title=f"Waiting time — {event}",
        related_object=instance,
        recipient=NotificationRecipientSpec(content_object=patient, phone_number=patient.phone_number),
        variable_values={
            "event": event,
            "service_name": describe_service(instance),
            "waiting_time": estimate_wait(instance),
        },
    )
