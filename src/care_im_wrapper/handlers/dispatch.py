from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
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


def track_previous_status(instance: Model, **kwargs: Any) -> None:
    """`pre_save` receiver stashing the pre-save `status` on the instance, so the paired
    `post_save` receiver can fire only on an actual transition. Connect it per model:

        pre_save.connect(track_previous_status, sender=TokenBooking)

    Reads `None` for an unsaved instance.
    """
    if instance.pk is None:
        instance._previous_status = None  # noqa: SLF001  # pyright: ignore[reportAttributeAccessIssue]
    else:
        instance._previous_status = (  # noqa: SLF001  # pyright: ignore[reportAttributeAccessIssue]
            type(instance).objects.filter(pk=instance.pk).values_list("status", flat=True).first()  # pyright: ignore[reportAttributeAccessIssue]
        )


def get_active_trigger(slug: str) -> NotificationTrigger | None:
    trigger = NotificationTrigger.objects.filter(slug=slug, is_active=True).first()
    if trigger is None:
        logger.warning("dispatch: no active NotificationTrigger with slug=%s, skipping", slug)
    return trigger


def get_matching_template(trigger: NotificationTrigger, channel: str) -> NotificationTemplate | None:
    template = NotificationTemplate.objects.filter(
        slug=trigger.template_slug,
        provider=channel,
        approval_status=TemplateApprovalStatus.ACTIVE,
        is_active=True,
    ).first()
    if template is None:
        logger.error(
            "dispatch: no active NotificationTemplate for channel=%s with slug=%s matching trigger %s, "
            "skipping event creation",
            channel,
            trigger.template_slug,
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
    """Shared entry point for signal handlers: resolves trigger/channel/template, creates event + recipient."""
    trigger = get_active_trigger(trigger_slug)
    if trigger is None:
        return None

    if not recipient.phone_number:
        # A recipient with no number can never be delivered and would only burn the dispatch
        # retry budget failing at send time. Skip it here instead.
        logger.info("fire_notification_event: skipping %s, recipient has no phone number", trigger_slug)
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
        variable_values={**(trigger.default_variable_values or {}), **variable_values},
    )

    notification_recipient = NotificationRecipient.objects.create(
        event=event,
        recipient_content_type=ContentType.objects.get_for_model(type(recipient.content_object)),
        recipient_object_id=recipient.content_object.pk,
        phone_number=recipient.phone_number,
        provider=channel,
    )

    # on_commit: ATOMIC_REQUESTS means a direct .delay() could race the worker ahead of the commit.
    from care_im_wrapper.tasks import dispatch_notification_recipient

    transaction.on_commit(
        lambda: dispatch_notification_recipient.delay(  # pyright: ignore[reportFunctionMemberAccess]
            notification_recipient.pk
        )
    )

    return event
