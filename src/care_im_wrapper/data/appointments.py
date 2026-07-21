"""Fetch appointment details for the authenticated actor."""

from care_im_wrapper.auth.actor import Actor
from care_im_wrapper.data.base import (
    ENTERED_IN_ERROR_STATUS,
    cached_fetch,
    humanize_choice,
    humanize_date,
    humanize_time,
)
from care_im_wrapper.data.common import resolve_target_patient
from care_im_wrapper.data.exceptions import NoDataError
from care_im_wrapper.data.records import AppointmentRecord
from care_im_wrapper.models import ConversationSession
from care_im_wrapper.settings import plugin_settings


@cached_fetch(timeout_seconds=int(plugin_settings.DATA_CACHE_TIMEOUT_SECONDS))
def fetch_appointments(actor: Actor, session: ConversationSession) -> list[AppointmentRecord]:
    """
    patient: returns their own last 10 appointments.
    staff:   returns appointments for session.active_patient_external_id.
    Raises PermissionDeniedError, NoDataError, MissingContextError.
    """
    from care.emr.models.scheduling.booking import TokenBooking  # type: ignore[import-untyped]

    patient = resolve_target_patient(actor, session)
    queryset = (
        TokenBooking.objects.filter(patient=patient)
        .exclude(status=ENTERED_IN_ERROR_STATUS)
        .select_related(
            "token_slot__resource__user",
            "token_slot__resource__facility",
            "token_slot__resource__location",
            "token_slot__resource__healthcare_service",
        )
    )
    records = queryset.order_by("-booked_on")[: int(plugin_settings.DATA_FETCH_LIMIT)]
    if not records:
        raise NoDataError

    appointment_records = []
    for booking in records:
        record = _extract_booking_info(booking)
        if record:
            appointment_records.append(record)

    # _extract_booking_info drops bookings with no usable slot, so a non-empty page can
    # still yield nothing renderable -- without this the user gets a bare header and no rows.
    if not appointment_records:
        raise NoDataError

    return appointment_records


def _extract_booking_info(booking) -> AppointmentRecord | None:
    slot = getattr(booking, "token_slot", None)
    status = humanize_choice(getattr(booking, "status", None))

    location = "Unknown"
    resource = getattr(slot, "resource", None) if slot else None

    if resource:
        res_type = getattr(resource, "resource_type", None)
        if res_type == "location":
            loc_obj = getattr(resource, "location", None)
            if loc_obj:
                location = getattr(loc_obj, "name", "Unknown")
        elif res_type == "healthcare_service":
            hs_obj = getattr(resource, "healthcare_service", None)
            if hs_obj:
                location = getattr(hs_obj, "name", "Unknown")
        else:
            facility = getattr(resource, "facility", None)
            if facility:
                location = getattr(facility, "name", "Unknown")

    practitioner = "Unknown"
    if resource:
        user = getattr(resource, "user", None)
        if user:
            first_name = getattr(user, "first_name", "")
            last_name = getattr(user, "last_name", "")
            practitioner = f"{first_name} {last_name}".strip() or "Unknown"

    date_str = ""
    time_slot = ""
    if slot and hasattr(slot, "start_datetime"):
        date_str = humanize_date(slot.start_datetime)
        time_slot = f"{humanize_time(slot.start_datetime)} - {humanize_time(slot.end_datetime)}"

    if not date_str or not time_slot:
        return None

    return AppointmentRecord(
        practitioner=practitioner,
        location=location,
        status=status,
        date=date_str,
        time_slot=time_slot,
    )
