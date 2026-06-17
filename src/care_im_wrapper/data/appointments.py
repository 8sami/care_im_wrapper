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
        status = humanize_choice(getattr(booking, "status", None))
        date = humanize_date(getattr(booking, "booked_on", None))
        info = _extract_booking_info(booking)
        items.append(f"{date} — {info} ({status})")

    return numbered_list("Your recent appointments:", items)


def _extract_booking_info(booking) -> str:
    token = getattr(booking, "token", None)
    slot = getattr(booking, "token_slot", None)
    if token and slot:
        return f"Token {token} (Slot: {slot})"
    return str(token or slot or "Unknown appointment")
