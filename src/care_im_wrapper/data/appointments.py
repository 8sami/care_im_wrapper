"""Fetch appointment details for the authenticated actor."""

from care_im_wrapper.auth.actor import Actor
from care_im_wrapper.data.base import cached_fetch, humanize_choice, humanize_date
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
    # N+1 risk: _extract_booking_info() below walks
    #   booking.token_slot.resource.user
    #   booking.token_slot.resource.facility
    # Add select_related("token_slot__resource__user", "token_slot__resource__facility")
    # in the upcoming N+1 review pass.
    queryset = TokenBooking.objects.filter(patient=patient)
    records = queryset.order_by("-booked_on")[: plugin_settings.DATA_FETCH_LIMIT]
    if not records:
        raise NoDataError

    appointment_records = []
    for booking in records:
        record = _extract_booking_info(booking)
        if record:
            appointment_records.append(record)

    return appointment_records


def _extract_booking_info(booking) -> AppointmentRecord | None:
    slot = getattr(booking, "token_slot", None)
    status = humanize_choice(getattr(booking, "status", None))

    practitioner = "Unknown"
    resource = getattr(slot, "resource", None) if slot else None
    if resource:
        user = getattr(resource, "user", None)
        if user:
            first_name = getattr(user, "first_name", "")
            last_name = getattr(user, "last_name", "")
            practitioner = f"{first_name} {last_name}".strip() or "Unknown"

    location = "Unknown"
    facility = getattr(resource, "facility", None) if resource else None
    if facility:
        location = getattr(facility, "name", "Unknown")

    date_str = ""
    time_slot = ""
    if slot and hasattr(slot, "start_datetime"):
        date_str = humanize_date(slot.start_datetime)
        time_slot = (
            f"{slot.start_datetime.strftime('%I:%M %p').lower()} - {slot.end_datetime.strftime('%I:%M %p').lower()}"
        )

    if not date_str or not time_slot:
        return None

    return AppointmentRecord(
        practitioner=practitioner,
        location=location,
        status=status,
        date=date_str,
        time_slot=time_slot,
    )
