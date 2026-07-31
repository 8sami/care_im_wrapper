from __future__ import annotations

import logging
import traceback
from datetime import timedelta
from typing import Any

from celery import shared_task
from celery.exceptions import MaxRetriesExceededError
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from care_im_wrapper.conversation.handlers import run_state_machine
from care_im_wrapper.core.rate_limit import is_outbound_rate_limited
from care_im_wrapper.documents.exceptions import DocumentUnavailableError
from care_im_wrapper.documents.service import (
    DIAGNOSTIC_REPORT_DOCUMENT_TYPE,
    DocumentRequest,
    get_system_document_link,
)
from care_im_wrapper.handlers.dispatch import NotificationRecipientSpec, fire_notification_event
from care_im_wrapper.messaging.exceptions import (
    OutboundRateLimitedError,
    WhatsAppBadRequestError,
    WhatsAppNetworkError,
    WhatsAppPairRateLimitError,
    WhatsAppServerError,
    WhatsAppTemplateNotConfiguredError,
)
from care_im_wrapper.messaging.normalize import normalize_status_update
from care_im_wrapper.messaging.registry import (
    get_min_send_interval_seconds,
    get_template_capable_providers,
    resolve_channel,
    send_template_message,
)
from care_im_wrapper.models.notification import (
    NotificationEvent,
    NotificationRecipient,
    NotificationStatus,
    NotificationStatusState,
)
from care_im_wrapper.settings import plugin_settings

logger = logging.getLogger(__name__)

_STATE_ORDER: dict[NotificationStatusState, int] = {
    NotificationStatusState.SENT: 0,
    NotificationStatusState.DELIVERED: 1,
    NotificationStatusState.READ: 2,
    NotificationStatusState.FAILED: 3,
}

# Never succeed on retry -- recorded as failed immediately instead of burning the budget.
_PERMANENT_SEND_ERRORS = (WhatsAppTemplateNotConfiguredError, WhatsAppBadRequestError)

DOCUMENT_READY_TRIGGER_SLUG = "document_ready_update"


def _failure_payload(exc: BaseException, attempt: int) -> dict[str, Any]:
    # Read at call time, not import time, so PLUGIN_CONFIGS overrides and reload() apply.
    error_max = int(plugin_settings.NOTIFICATION_FAILURE_ERROR_MAX_CHARS)
    traceback_max = int(plugin_settings.NOTIFICATION_FAILURE_TRACEBACK_MAX_CHARS)
    return {
        "error_type": type(exc).__name__,
        "error": str(exc)[:error_max],
        "traceback": "".join(traceback.format_exception(exc))[:traceback_max],
        # 0 for a permanent error, TASK_MAX_RETRIES for a transient one that exhausted them.
        "attempt": attempt,
    }


def _claim_for_dispatch(recipient_id: int) -> bool:
    """Takes exclusive ownership of sending this recipient. True if we got it."""
    return (
        NotificationRecipient.objects.filter(pk=recipient_id, dispatch_started_at__isnull=True).update(
            dispatch_started_at=timezone.now()
        )
        == 1
    )


def _release_claim(recipient_id: int) -> None:
    """Hands a recipient back to the sweep without waiting for the claim to go stale."""
    NotificationRecipient.objects.filter(pk=recipient_id).update(dispatch_started_at=None)


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
    time_limit=int(plugin_settings.INBOUND_TASK_TIME_LIMIT_SECONDS),
)
def process_inbound_message(
    self,
    phone_number: str,
    text: str,
    channel: str,
    raw_id: str | None = None,
) -> None:
    if raw_id and self.request.retries == 0:
        dup_key = f"msg_seen:{raw_id}"
        if not cache.add(dup_key, True, timeout=plugin_settings.MESSAGE_DEDUP_TIMEOUT_SECONDS):
            logger.info("Duplicate message detected (ID: %s). Dropping.", raw_id)
            return

    cache.delete(f"pending_task:{phone_number}")

    try:
        run_state_machine(phone_number, text, channel)
    except OutboundRateLimitedError as exc:
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

        with transaction.atomic():
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

    if self.request.retries == 0 and not _claim_for_dispatch(recipient_id):
        logger.info(
            "dispatch_notification_recipient: recipient %s is already claimed by another worker, skipping",
            recipient_id,
        )
        return

    if is_outbound_rate_limited(recipient.provider, recipient.phone_number, is_urgent=recipient.event.is_urgent):
        try:
            raise self.retry(countdown=get_min_send_interval_seconds(recipient.provider))
        except MaxRetriesExceededError:
            logger.info(
                "dispatch_notification_recipient: recipient %s still paced after %s retries, "
                "releasing for the periodic sweep",
                recipient_id,
                self.request.retries,
            )
            _release_claim(recipient_id)
            return

    try:
        sent = send_template_message(
            channel=recipient.provider,
            to=recipient.phone_number,
            template=recipient.event.template,
            related_object=recipient.event.related_object,
            event_variable_values=recipient.event.variable_values,
            recipient_variable_overrides=recipient.variable_overrides,
        )
    except _PERMANENT_SEND_ERRORS as exc:
        logger.error("dispatch_notification_recipient: permanent send error for recipient %s: %s", recipient_id, exc)
        _record_failure(recipient, _failure_payload(exc, self.request.retries))
        return
    except Exception as exc:
        logger.exception("dispatch_notification_recipient: send failed for recipient %s", recipient_id)
        try:
            raise self.retry(exc=exc) from exc
        except MaxRetriesExceededError:
            _record_failure(recipient, _failure_payload(exc, self.request.retries))
            return

    if sent.tracking_id is None:
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

    recipient.tracking_id = sent.tracking_id
    recipient.message_payload = {
        "template_slug": recipient.event.template.slug,
        "event_variable_values": recipient.event.variable_values,
        "recipient_variable_overrides": recipient.variable_overrides,
        # The resolved values actually sent, for auditing what the recipient received.
        "sent_parameters": sent.parameters,
    }
    recipient.save(update_fields=["tracking_id", "message_payload"])

    NotificationStatus.objects.create(
        recipient=recipient,
        state=NotificationStatusState.SENT,
        payload=None,
    )
    recipient.latest_status = NotificationStatusState.SENT
    recipient.save(update_fields=["latest_status"])


@shared_task
def notify_document_ready(report_external_id: str) -> None:
    """Mints a document link for a finalised DiagnosticReport and fires its notification."""
    from care.emr.models.diagnostic_report import DiagnosticReport  # pyright: ignore[reportMissingImports]

    report = DiagnosticReport.objects.filter(external_id=report_external_id).select_related("patient").first()
    if report is None:
        logger.warning("notify_document_ready: no DiagnosticReport with external_id=%s", report_external_id)
        return

    patient = report.patient
    provider = resolve_channel(patient.phone_number)
    document_request = DocumentRequest(
        document_type=DIAGNOSTIC_REPORT_DOCUMENT_TYPE,
        encounter=report.encounter,
        diagnostic_report=report,
    )

    try:
        link = get_system_document_link(patient, document_request, provider)
    except DocumentUnavailableError:
        logger.warning(
            "notify_document_ready: document unavailable for report %s, skipping notification",
            report_external_id,
        )
        return

    fire_notification_event(
        trigger_slug=DOCUMENT_READY_TRIGGER_SLUG,
        title=f"Document ready — {report.external_id}",
        related_object=report,
        recipient=NotificationRecipientSpec(content_object=patient, phone_number=patient.phone_number),
        variable_values={
            "document_type": document_request.document_type,
            "document_url_suffix": link.token,
        },
    )


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
def send_appointment_reminders() -> None:
    """Reminds patients about bookings starting inside the lead window."""
    from care.emr.models.scheduling.booking import TokenBooking  # pyright: ignore[reportMissingImports]
    from care.emr.resources.scheduling.slot.spec import BookingStatusChoices  # pyright: ignore[reportMissingImports]

    from care_im_wrapper.handlers.booking import describe_booking_resource

    trigger_slug = plugin_settings.APPOINTMENT_REMINDER_TRIGGER_SLUG
    now = timezone.now()
    window_end = now + timedelta(seconds=int(plugin_settings.APPOINTMENT_REMINDER_LEAD_SECONDS))

    bookings = (
        TokenBooking.objects.filter(
            status=BookingStatusChoices.booked,
            token_slot__start_datetime__gte=now,
            token_slot__start_datetime__lte=window_end,
        )
        .select_related(
            "patient",
            "token_slot__resource__user",
            "token_slot__resource__facility",
        )
        .order_by("token_slot__start_datetime")
    )
    if not bookings:
        return

    booking_content_type = ContentType.objects.get_for_model(TokenBooking)
    already_reminded = set(
        NotificationEvent.objects.filter(
            trigger__slug=trigger_slug,
            related_object_content_type=booking_content_type,
            related_object_id__in=[booking.pk for booking in bookings],
        ).values_list("related_object_id", flat=True)
    )

    for booking in bookings:
        if booking.pk in already_reminded:
            continue
        fire_notification_event(
            trigger_slug=trigger_slug,
            title=f"Appointment reminder — {booking.external_id}",
            related_object=booking,
            recipient=NotificationRecipientSpec(
                content_object=booking.patient, phone_number=booking.patient.phone_number
            ),
            variable_values={
                "event": "appointment",
                "event_header": "appointment",
                "doctor_name": describe_booking_resource(booking),
            },
        )


@shared_task
def dispatch_pending_notification_recipients() -> None:
    """Safety net for recipients real-time dispatch never delivered."""
    stale_cutoff = timezone.now() - timedelta(seconds=int(plugin_settings.DISPATCH_CLAIM_STALE_SECONDS))
    recipients = (
        NotificationRecipient.objects.filter(
            latest_status__isnull=True,
            event__deleted=False,
        )
        .filter(Q(dispatch_started_at__isnull=True) | Q(dispatch_started_at__lt=stale_cutoff))
        .select_related("event")
    )
    for recipient in recipients:
        # Clear a stale claim so the task's own first-attempt claim can succeed.
        if recipient.dispatch_started_at is not None:
            logger.warning(
                "dispatch_pending_notification_recipients: reclaiming recipient %s, claimed at %s and never completed",
                recipient.pk,
                recipient.dispatch_started_at,
            )
            _release_claim(recipient.pk)
        dispatch_notification_recipient.delay(recipient.pk)  # pyright: ignore[reportCallIssue]
