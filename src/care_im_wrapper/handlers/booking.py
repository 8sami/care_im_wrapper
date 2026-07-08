import logging
from typing import Any

from care.emr.models.scheduling.booking import TokenBooking  # pyright: ignore[reportMissingImports]
from care.emr.resources.scheduling.slot.spec import BookingStatusChoices  # pyright: ignore[reportMissingImports]
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from care_im_wrapper.handlers.dispatch import NotificationRecipientSpec, fire_notification_event
from care_im_wrapper.models.notification import _FACILITY_RESOLVERS
from care_im_wrapper.settings import plugin_settings

logger = logging.getLogger(__name__)


def _resolve_booking_facility(booking: TokenBooking) -> Any | None:
    return booking.token_slot.resource.facility


_FACILITY_RESOLVERS[TokenBooking] = _resolve_booking_facility


def _build_variable_values(booking: TokenBooking) -> dict[str, Any]:
    token_slot = booking.token_slot
    resource = token_slot.resource
    values: dict[str, Any] = {
        "patient_name": booking.patient.name,
        "appointment_datetime": timezone.localtime(token_slot.start_datetime).strftime("%d %b %Y, %I:%M %p"),
        "practitioner_name": resource.user.full_name if resource.user_id else "",
    }
    facility = resource.facility
    if facility is not None:
        values["facility_name"] = facility.name

    video_connect_link = resource.user.video_connect_link if resource.user_id else None
    if video_connect_link:
        values["teleconsultation_link"] = video_connect_link

    return values


def _create_event_and_recipient(booking: TokenBooking, trigger_slug: str, title: str) -> None:
    fire_notification_event(
        trigger_slug=trigger_slug,
        title=title,
        related_object=booking,
        recipient=NotificationRecipientSpec(
            content_object=booking.patient,
            phone_number=booking.patient.phone_number,
        ),
        variable_values=_build_variable_values(booking),
    )


@receiver(pre_save, sender=TokenBooking)
def on_booking_pre_save(sender: type[TokenBooking], instance: TokenBooking, **kwargs: Any) -> None:
    if instance.pk is None:
        instance._previous_status = None  # pyright: ignore[reportAttributeAccessIssue]
    else:
        instance._previous_status = (  # pyright: ignore[reportAttributeAccessIssue]
            TokenBooking.objects.filter(pk=instance.pk).values_list("status", flat=True).first()
        )


@receiver(post_save, sender=TokenBooking)
def on_booking_post_save(sender: type[TokenBooking], instance: TokenBooking, created: bool, **kwargs: Any) -> None:
    trigger_slugs = plugin_settings.APPOINTMENT_TRIGGER_SLUGS

    if created:
        if instance.status == BookingStatusChoices.booked:
            _create_event_and_recipient(
                instance, trigger_slugs["booked"], f"Appointment confirmed — {instance.external_id}"
            )
        return

    previous_status = getattr(instance, "_previous_status", None)
    if previous_status == instance.status:
        return

    if instance.status == BookingStatusChoices.cancelled:
        _create_event_and_recipient(
            instance, trigger_slugs["cancelled"], f"Appointment cancelled — {instance.external_id}"
        )
    elif instance.status == BookingStatusChoices.rescheduled:
        _create_event_and_recipient(
            instance, trigger_slugs["rescheduled"], f"Appointment rescheduled — {instance.external_id}"
        )
