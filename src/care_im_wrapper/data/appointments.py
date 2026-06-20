"""Fetch appointment details for the authenticated actor."""

from care_im_wrapper.auth.actor import Actor
from care_im_wrapper.data.base import humanize_choice, humanize_date, numbered_list
from care_im_wrapper.data.common import resolve_target_patient
from care_im_wrapper.data.exceptions import NoDataError
from care_im_wrapper.models import ConversationSession


def fetch_appointments(actor: Actor, session: ConversationSession) -> str:
    """
    patient: returns their own last 10 appointments.
    staff:   returns appointments for session.active_patient_external_id.
    Raises PermissionDeniedError, NoDataError, MissingContextError.
    """
    from care.emr.models.scheduling.booking import TokenBooking  # type: ignore[import-untyped]

    patient = resolve_target_patient(actor, session)
    queryset = TokenBooking.objects.filter(patient=patient)
    records = queryset.order_by("-booked_on")[:10]
    if not records:
        raise NoDataError

    items = []
    for booking in records:
        info = _extract_booking_info(booking)
        items.append(info)

    return numbered_list("Your recent appointments:", items)


def _extract_booking_info(booking) -> str:
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
    time_str = ""
    if slot and hasattr(slot, "start_datetime"):
        date_str = humanize_date(slot.start_datetime)
        time_str = (
            f"{slot.start_datetime.strftime('%I:%M %p').lower()} - {slot.end_datetime.strftime('%I:%M %p').lower()}"
        )

    lines = [
        f"Practitioner: {practitioner}",
        f"      Location: {location}",
        f"      Status: {status.capitalize() if status else ''}",
        f"      Date: {date_str}",
        f"      Time Slot: {time_str}",
    ]

    return "\n".join(line for line in lines if line.split(": ", 1)[1].strip())
