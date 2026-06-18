"""Fetch encounter details for the authenticated actor."""

from care_im_wrapper.auth.actor import Actor
from care_im_wrapper.data.base import humanize_choice, humanize_date, numbered_list
from care_im_wrapper.data.common import resolve_target_patient
from care_im_wrapper.data.exceptions import NoDataError
from care_im_wrapper.models import ConversationSession


def fetch_encounters(actor: Actor, session: ConversationSession) -> str:
    """
    patient: returns their own last 10 encounters.
    staff:   returns encounters for session.active_patient_external_id.
    Raises PermissionDeniedError, NoDataError, MissingContextError.
    """
    from care.emr.models.encounter import Encounter  # type: ignore[import-untyped]

    patient = resolve_target_patient(actor, session)
    queryset = Encounter.objects.filter(patient=patient)
    records = queryset.order_by("-created_date")[:10]
    if not records:
        raise NoDataError

    items = []
    for enc in records:
        status = humanize_choice(getattr(enc, "status", None))
        date = humanize_date(getattr(enc, "created_date", None))
        items.append(f"{date} — {fmt_facility_name(enc)} ({status})")

    return numbered_list("Your recent encounters:", items)


def fmt_facility_name(enc) -> str:
    if hasattr(enc, "facility") and enc.facility:
        return getattr(enc.facility, "name", "Unknown facility")
    return "Unknown facility"
