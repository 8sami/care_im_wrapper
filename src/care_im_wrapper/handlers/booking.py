import logging
from typing import Any

from care.emr.models.scheduling.booking import TokenBooking  # pyright: ignore[reportMissingImports]
from care.emr.resources.scheduling.slot.spec import BookingStatusChoices  # pyright: ignore[reportMissingImports]
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from care_im_wrapper.handlers.dispatch import (
    NotificationRecipientSpec,
    fire_notification_event,
    track_previous_status,
)
from care_im_wrapper.models.notification import _FACILITY_RESOLVERS
from care_im_wrapper.reports.context_builders import NOTIFICATION_CONTEXT_REGISTRY, TokenBookingContext
from care_im_wrapper.settings import plugin_settings

logger = logging.getLogger(__name__)

# Slug naming the TokenBooking context; set on the appointment triggers' context_slug.
BOOKING_CONTEXT_SLUG = "token_booking"


def _resolve_booking_facility(booking: TokenBooking) -> Any | None:
    return booking.token_slot.resource.facility


_FACILITY_RESOLVERS[TokenBooking] = _resolve_booking_facility
NOTIFICATION_CONTEXT_REGISTRY.register(BOOKING_CONTEXT_SLUG, TokenBookingContext)


def _create_event_and_recipient(booking: TokenBooking, trigger_slug: str, title: str) -> None:
    # Field values are resolved from trigger.default_variable_values and booking at send time.
    fire_notification_event(
        trigger_slug=trigger_slug,
        title=title,
        related_object=booking,
        recipient=NotificationRecipientSpec(
            content_object=booking.patient,
            phone_number=booking.patient.phone_number,
        ),
        variable_values={},
    )


pre_save.connect(track_previous_status, sender=TokenBooking)


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
