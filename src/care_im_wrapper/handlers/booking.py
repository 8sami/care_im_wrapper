import logging
from typing import Any

from care.emr.models.scheduling.booking import TokenBooking  # pyright: ignore[reportMissingImports]
from care.emr.resources.scheduling.slot.spec import BookingStatusChoices  # pyright: ignore[reportMissingImports]
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from care_im_wrapper.data.base import describe_resource
from care_im_wrapper.handlers.dispatch import (
    NotificationRecipientSpec,
    fire_notification_event,
    track_previous_field,
)
from care_im_wrapper.models.notification import _FACILITY_RESOLVERS
from care_im_wrapper.reports.context_builders import (
    NOTIFICATION_CONTEXT_REGISTRY,
    AppointmentReminderContext,
    TokenBookingContext,
)
from care_im_wrapper.settings import plugin_settings

logger = logging.getLogger(__name__)

# Slug naming the TokenBooking context; set on the appointment triggers' context_slug.
BOOKING_CONTEXT_SLUG = "token_booking"
APPOINTMENT_REMINDER_CONTEXT_SLUG = "appointment_reminder"


def _resolve_booking_facility(booking: TokenBooking) -> Any | None:
    return booking.token_slot.resource.facility


_FACILITY_RESOLVERS[TokenBooking] = _resolve_booking_facility
NOTIFICATION_CONTEXT_REGISTRY.register(BOOKING_CONTEXT_SLUG, TokenBookingContext)
NOTIFICATION_CONTEXT_REGISTRY.register(APPOINTMENT_REMINDER_CONTEXT_SLUG, AppointmentReminderContext)


def describe_booking_resource(booking: TokenBooking) -> str:
    """Who or what the appointment is with. Supplied as a flat value rather than read off."""
    return describe_resource(getattr(booking.token_slot, "resource", None))


def _create_event_and_recipient(booking: TokenBooking, trigger_slug: str, title: str) -> None:
    fire_notification_event(
        trigger_slug=trigger_slug,
        title=title,
        related_object=booking,
        recipient=NotificationRecipientSpec(
            content_object=booking.patient,
            phone_number=booking.patient.phone_number,
        ),
        variable_values={"doctor_name": describe_booking_resource(booking)},
    )


# Booking status -> (APPOINTMENT_TRIGGER_SLUGS key, phrase the event title reads with).
STATUS_NOTIFICATIONS: dict[str, tuple[str, str]] = {
    BookingStatusChoices.cancelled.value: ("cancelled", "cancelled"),
    BookingStatusChoices.rescheduled.value: ("rescheduled", "rescheduled"),
    BookingStatusChoices.noshow.value: ("noshow", "marked no-show"),
    BookingStatusChoices.checked_in.value: ("checked_in", "checked in"),
    BookingStatusChoices.in_consultation.value: ("in_consultation", "in consultation"),
    BookingStatusChoices.fulfilled.value: ("fulfilled", "fulfilled"),
}


pre_save.connect(track_previous_field("status"), sender=TokenBooking, weak=False)


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

    notification = STATUS_NOTIFICATIONS.get(instance.status)
    if notification is None:
        return

    slug_key, title_phrase = notification
    _create_event_and_recipient(
        instance, trigger_slugs[slug_key], f"Appointment {title_phrase} — {instance.external_id}"
    )
