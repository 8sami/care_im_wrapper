from __future__ import annotations

import logging
from typing import Any

from celery import shared_task
from django.core.cache import cache

from care_im_wrapper.conversation.handlers import run_state_machine
from care_im_wrapper.core.rate_limit import is_outbound_rate_limited
from care_im_wrapper.messaging.exceptions import (
    WhatsAppBadRequestError,
    WhatsAppNetworkError,
    WhatsAppPairRateLimitError,
    WhatsAppServerError,
)
from care_im_wrapper.messaging.normalize import normalize_status_update
from care_im_wrapper.messaging.registry import (
    get_min_send_interval_seconds,
    get_template_capable_providers,
    send_template_message,
)
from care_im_wrapper.models.notification import NotificationRecipient, NotificationStatus, NotificationStatusState
from care_im_wrapper.settings import plugin_settings

logger = logging.getLogger(__name__)

_STATE_ORDER: dict[NotificationStatusState, int] = {
    NotificationStatusState.SENT: 0,
    NotificationStatusState.DELIVERED: 1,
    NotificationStatusState.READ: 2,
    NotificationStatusState.FAILED: 3,
}


@shared_task(
    bind=True,
    max_retries=plugin_settings.TASK_MAX_RETRIES,
    default_retry_delay=plugin_settings.TASK_RETRY_DELAY_SECONDS,
    time_limit=int(plugin_settings.DEBOUNCE_SECONDS + plugin_settings.TASK_EXECUTION_BUFFER_SECONDS),
)
def process_inbound_message(
    self,
    phone_number: str,
    text: str,
    channel: str,
    raw_id: str | None = None,
) -> None:
    if raw_id:
        dup_key = f"msg_seen:{raw_id}"
        if not cache.add(dup_key, True, timeout=plugin_settings.MESSAGE_DEDUP_TIMEOUT_SECONDS):
            logger.info("Duplicate message detected (ID: %s). Dropping.", raw_id)
            return

    cache.delete(f"pending_task:{phone_number}")

    try:
        run_state_machine(phone_number, text, channel)
    except (WhatsAppPairRateLimitError, WhatsAppNetworkError, WhatsAppServerError) as exc:
        logger.warning("Transient WhatsApp error for %s: %s. Retrying.", phone_number, exc)
        raise self.retry(exc=exc) from exc
    except WhatsAppBadRequestError as exc:
        logger.error("Permanent WhatsApp error for %s: %s. Dropping message.", phone_number, exc)
    except Exception as exc:
        logger.error("process_inbound_message failed: %s", exc)
        raise self.retry(exc=exc) from exc


@shared_task(
    bind=True,
    max_retries=plugin_settings.TASK_MAX_RETRIES,
    default_retry_delay=plugin_settings.TASK_RETRY_DELAY_SECONDS,
)
def process_status_update(self, payload: dict[str, Any], channel: str) -> None:
    try:
        update = normalize_status_update(payload, channel)
        if update is None:
            logger.warning("process_status_update: could not normalize payload, dropping")
            return

        recipient = NotificationRecipient.objects.filter(tracking_id=update.tracking_id).first()
        if recipient is None:
            logger.info(
                "process_status_update: no NotificationRecipient with tracking_id=%s",
                update.tracking_id,
            )
            return

        NotificationStatus.objects.create(
            recipient=recipient,
            state=update.state,
            payload=update.raw_payload,
        )

        if _STATE_ORDER.get(recipient.latest_status, -1) <= _STATE_ORDER[update.state]:
            recipient.latest_status = update.state
            recipient.save(update_fields=["latest_status"])
    except Exception as exc:
        logger.error("process_status_update failed: %s", exc)
        raise self.retry(exc=exc) from exc


@shared_task(
    bind=True,
    max_retries=plugin_settings.TASK_MAX_RETRIES,
    default_retry_delay=plugin_settings.TASK_RETRY_DELAY_SECONDS,
)
def dispatch_notification_recipient(self, recipient_id: int) -> None:
    recipient = NotificationRecipient.objects.select_related("event__template").filter(pk=recipient_id).first()
    if recipient is None:
        logger.error("dispatch_notification_recipient: no NotificationRecipient with pk=%s", recipient_id)
        return

    if recipient.latest_status is not None:
        logger.info(
            "dispatch_notification_recipient: recipient %s already has latest_status=%s, skipping",
            recipient_id,
            recipient.latest_status,
        )
        return

    if is_outbound_rate_limited(recipient.provider, recipient.phone_number, is_urgent=recipient.event.is_urgent):
        raise self.retry(countdown=get_min_send_interval_seconds(recipient.provider))

    try:
        tracking_id = send_template_message(
            channel=recipient.provider,
            to=recipient.phone_number,
            template=recipient.event.template,
            related_object=recipient.event.related_object,
            event_variable_values=recipient.event.variable_values,
            recipient_variable_overrides=recipient.variable_overrides,
        )
    except Exception as exc:
        NotificationStatus.objects.create(
            recipient=recipient,
            state=NotificationStatusState.FAILED,
            payload={"error": str(exc)[:500]},
        )
        recipient.latest_status = NotificationStatusState.FAILED
        recipient.save(update_fields=["latest_status"])
        raise self.retry(exc=exc) from exc

    if tracking_id is None:
        logger.error(
            "dispatch_notification_recipient: send_template_message returned no tracking id for recipient %s",
            recipient_id,
        )
        NotificationStatus.objects.create(
            recipient=recipient,
            state=NotificationStatusState.FAILED,
            payload=None,
        )
        recipient.latest_status = NotificationStatusState.FAILED
        recipient.save(update_fields=["latest_status"])
        return

    recipient.tracking_id = tracking_id
    recipient.message_payload = {
        "template_slug": recipient.event.template.slug,
        "event_variable_values": recipient.event.variable_values,
        "recipient_variable_overrides": recipient.variable_overrides,
    }
    recipient.save(update_fields=["tracking_id", "message_payload"])

    NotificationStatus.objects.create(
        recipient=recipient,
        state=NotificationStatusState.SENT,
        payload=None,
    )
    recipient.latest_status = NotificationStatusState.SENT
    recipient.save(update_fields=["latest_status"])


@shared_task(
    bind=True,
    max_retries=plugin_settings.TASK_MAX_RETRIES,
    default_retry_delay=plugin_settings.TASK_RETRY_DELAY_SECONDS,
)
def sync_notification_templates(self) -> None:
    for channel, client in get_template_capable_providers():
        try:
            client.sync_templates()
        except Exception as exc:
            logger.error("sync_notification_templates: sync failed for provider %s: %s", channel, exc)


@shared_task
def dispatch_pending_notification_recipients() -> None:
    recipients = NotificationRecipient.objects.filter(
        latest_status__isnull=True,
        event__deleted=False,
    ).select_related("event")
    for recipient in recipients:
        dispatch_notification_recipient.delay(recipient.pk)  # pyright: ignore[reportCallIssue]
