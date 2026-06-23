"""Fetch patient summary for the authenticated actor."""

from care_im_wrapper.auth.actor import Actor
from care_im_wrapper.conversation.templates import _msg
from care_im_wrapper.data.base import cached_fetch, field, humanize_choice
from care_im_wrapper.data.common import resolve_target_patient
from care_im_wrapper.models import ConversationSession
from care_im_wrapper.settings import plugin_settings


@cached_fetch(timeout_seconds=int(plugin_settings.DATA_CACHE_TIMEOUT_SECONDS))
def fetch_summary(actor: Actor, session: ConversationSession) -> str:
    """
    patient: returns their own summary.
    staff:   returns summary for session.active_patient_external_id.
    Raises PermissionDeniedError, NoDataError, MissingContextError.
    """
    patient = resolve_target_patient(actor, session)

    dob_display = _format_dob_or_yob(patient)

    lines = [
        field("Name", getattr(patient, "name", None)),
        field("Date of Birth", dob_display),
        field("Blood Group", humanize_choice(getattr(patient, "blood_group", None))),
        field("Gender", humanize_choice(getattr(patient, "gender", None))),
        field("Phone", getattr(patient, "phone_number", None)),
    ]

    return _msg("summary_header") + "\n\n" + "\n".join(lines)


def _format_dob_or_yob(patient) -> str | None:
    """
    If DOB is empty fall back to year_of_birth
    """
    dob = getattr(patient, "date_of_birth", None)
    if dob:
        return dob.strftime("%d %b %Y")
    yob = getattr(patient, "year_of_birth", None)
    if yob:
        return f"Year of birth: {yob}"
    return None
