from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.db.models import Model

from care_im_wrapper.messaging.registry import resolve_channel
from care_im_wrapper.models.notification import (
    NotificationEvent,
    NotificationRecipient,
    NotificationTemplate,
    NotificationTrigger,
    TemplateApprovalStatus,
)

logger = logging.getLogger(__name__)


@dataclass
class NotificationRecipientSpec:
    """Who a fired notification event should be delivered to."""

    content_object: Model
    phone_number: str


def get_active_trigger(slug: str) -> NotificationTrigger | None:
    trigger = NotificationTrigger.objects.filter(slug=slug, is_active=True).first()
    if trigger is None:
        logger.warning("dispatch: no active NotificationTrigger with slug=%s, skipping", slug)
    return trigger


def get_matching_template(trigger: NotificationTrigger, channel: str) -> NotificationTemplate | None:
    template = NotificationTemplate.objects.filter(
        slug=trigger.slug,
        provider=channel,
        approval_status=TemplateApprovalStatus.ACTIVE,
        is_active=True,
    ).first()
    if template is None:
        logger.error(
            "dispatch: no active NotificationTemplate for channel=%s with slug=%s matching trigger, "
            "skipping event creation",
            channel,
            trigger.slug,
        )
    return template


def fire_notification_event(
    *,
    trigger_slug: str,
    title: str,
    related_object: Model,
    recipient: NotificationRecipientSpec,
    variable_values: dict[str, Any],
) -> NotificationEvent | None:
    """
    Shared entry point for every signal handler that needs to create a
    NotificationEvent + NotificationRecipient pair. Resolves the trigger,
    channel, and matching template once here, so individual handlers
    (booking.py, and any future ones) only supply what's actually specific to
    their domain: the related object, the recipient, and the rendered
    variable values. Returns None (and logs why) if no active trigger or no
    matching template was found, without raising.
    """
    trigger = get_active_trigger(trigger_slug)
    if trigger is None:
        return None

    channel = resolve_channel(recipient.phone_number)
    template = get_matching_template(trigger, channel)
    if template is None:
        return None

    event = NotificationEvent.objects.create(
        template=template,
        trigger=trigger,
        title=title,
        related_object_content_type=ContentType.objects.get_for_model(type(related_object)),
        related_object_id=related_object.pk,
        variable_values=variable_values,
    )

    NotificationRecipient.objects.create(
        event=event,
        recipient_content_type=ContentType.objects.get_for_model(type(recipient.content_object)),
        recipient_object_id=recipient.content_object.pk,
        phone_number=recipient.phone_number,
        provider=channel,
    )

    return event
