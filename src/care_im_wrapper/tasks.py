from __future__ import annotations

import logging
import traceback
from typing import Any

from celery import shared_task
from django.core.cache import cache

from care_im_wrapper.conversation.handlers import run_state_machine
from care_im_wrapper.core.rate_limit import is_outbound_rate_limited
from care_im_wrapper.messaging.exceptions import (
    OutboundRateLimitedError,
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

# Provider exceptions carry the whole upstream response body in str(exc); bounded so one
# bad response can't bloat the JSONB row.
_MAX_ERROR_CHARS = 2000
_MAX_TRACEBACK_CHARS = 8000


def _failure_payload(exc: BaseException, attempt: int) -> dict[str, Any]:
    return {
        "error_type": type(exc).__name__,
        "error": str(exc)[:_MAX_ERROR_CHARS],
        "traceback": "".join(traceback.format_exception(exc))[:_MAX_TRACEBACK_CHARS],
        # Always 0 today: the latest_status guard in dispatch_notification_recipient makes
        # every retry return early, so no retry reaches here. Non-zero means that changed.
        "attempt": attempt,
    }


def _record_failure(recipient: NotificationRecipient, payload: dict[str, Any]) -> None:
    NotificationStatus.objects.create(
        recipient=recipient,
        state=NotificationStatusState.FAILED,
        payload=payload,
    )
    recipient.latest_status = NotificationStatusState.FAILED
    recipient.save(update_fields=["latest_status"])


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
    # Only guard against Meta re-delivering the same webhook on the first attempt --
    # self.retry() re-invokes this exact function for the *same* raw_id, and that must
    # not be mistaken for a duplicate delivery or every retry path silently no-ops.
    if raw_id and self.request.retries == 0:
        dup_key = f"msg_seen:{raw_id}"
        if not cache.add(dup_key, True, timeout=plugin_settings.MESSAGE_DEDUP_TIMEOUT_SECONDS):
            logger.info("Duplicate message detected (ID: %s). Dropping.", raw_id)
            return

    cache.delete(f"pending_task:{phone_number}")

    try:
        run_state_machine(phone_number, text, channel)
    except OutboundRateLimitedError as exc:
        # Proactively paced (see messaging.registry.send_message) -- retry after the
        # provider's own minimum send interval instead of the generic 60s task delay,
        # so a burst of inbound messages doesn't trickle out replies for minutes.
        countdown = get_min_send_interval_seconds(channel)
        logger.info("Outbound rate-limited for %s on %s. Retrying in %ss.", phone_number, channel, countdown)
        raise self.retry(exc=exc, countdown=countdown) from exc
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
            logger.debug(
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
        logger.exception("dispatch_notification_recipient: send failed for recipient %s", recipient_id)
        _record_failure(recipient, _failure_payload(exc, self.request.retries))
        raise self.retry(exc=exc) from exc

    if tracking_id is None:
        logger.error(
            "dispatch_notification_recipient: send_template_message returned no tracking id for recipient %s",
            recipient_id,
        )
        _record_failure(
            recipient,
            {
                "error_type": "MissingTrackingId",
                "error": "Provider accepted the request but returned no message id, so delivery cannot be tracked.",
                "attempt": self.request.retries,
            },
        )
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
