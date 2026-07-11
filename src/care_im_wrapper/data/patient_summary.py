"""Fetch patient summary for the authenticated actor."""

from care_im_wrapper.auth.actor import Actor
from care_im_wrapper.core.sanitize import mask_phone_number
from care_im_wrapper.data.base import cached_fetch, humanize_choice, humanize_date
from care_im_wrapper.data.common import resolve_target_patient
from care_im_wrapper.data.records import PatientSummary
from care_im_wrapper.models import ConversationSession
from care_im_wrapper.settings import plugin_settings


@cached_fetch(timeout_seconds=int(plugin_settings.DATA_CACHE_TIMEOUT_SECONDS))
def fetch_summary(actor: Actor, session: ConversationSession) -> PatientSummary:
    """
    patient: returns their own summary.
    staff:   returns summary for session.active_patient_external_id.
    Raises PermissionDeniedError, NoDataError, MissingContextError.
    """
    patient = resolve_target_patient(actor, session)

    dob_display = _format_dob_or_yob(patient)

    return PatientSummary(
        name=getattr(patient, "name", None),
        date_of_birth=dob_display,
        blood_group=humanize_choice(getattr(patient, "blood_group", None)),
        gender=humanize_choice(getattr(patient, "gender", None)),
        phone=mask_phone_number(getattr(patient, "phone_number", None) or ""),
    )


def _format_dob_or_yob(patient) -> str | None:
    """
    If DOB is empty fall back to year_of_birth
    """
    dob = getattr(patient, "date_of_birth", None)
    if dob:
        return humanize_date(dob)
    yob = getattr(patient, "year_of_birth", None)
    if yob:
        return f"Year of birth: {yob}"
    return None
