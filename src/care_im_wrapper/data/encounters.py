"""Fetch encounter details for the authenticated actor."""

from care_im_wrapper.auth.actor import Actor
from care_im_wrapper.data.base import (
    ENTERED_IN_ERROR_STATUS,
    cached_fetch,
    humanize_choice,
    humanize_date,
    humanize_encounter_class,
)
from care_im_wrapper.data.common import resolve_target_patient
from care_im_wrapper.data.exceptions import NoDataError
from care_im_wrapper.data.records import EncounterRecord
from care_im_wrapper.models import ConversationSession
from care_im_wrapper.settings import plugin_settings


@cached_fetch(timeout_seconds=int(plugin_settings.DATA_CACHE_TIMEOUT_SECONDS))
def fetch_encounters(actor: Actor, session: ConversationSession) -> list[EncounterRecord]:
    """
    patient: returns their own last 10 encounters.
    staff:   returns encounters for session.active_patient_external_id.
    Raises PermissionDeniedError, NoDataError, MissingContextError.
    """
    from care.emr.models.encounter import Encounter  # type: ignore[import-untyped]

    patient = resolve_target_patient(actor, session)
    queryset = (
        Encounter.objects.filter(patient=patient).exclude(status=ENTERED_IN_ERROR_STATUS).select_related("facility")
    )
    records = queryset.order_by("-created_date")[: plugin_settings.DATA_FETCH_LIMIT]
    if not records:
        raise NoDataError

    encounter_records = []
    for enc in records:
        status = humanize_choice(getattr(enc, "status", None))
        date = humanize_date(getattr(enc, "created_date", None))
        facility = fmt_facility_name(enc)
        encounter_class = humanize_encounter_class(getattr(enc, "encounter_class", None))
        encounter_records.append(
            EncounterRecord(date=date, facility=facility, status=status, encounter_class=encounter_class)
        )

    return encounter_records


def fmt_facility_name(enc) -> str:
    if hasattr(enc, "facility") and enc.facility:
        return getattr(enc.facility, "name", "Unknown facility")
    return "Unknown facility"
