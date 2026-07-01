"""Fetch procedure/service request details for the authenticated actor."""

from care_im_wrapper.auth.actor import Actor
from care_im_wrapper.data.base import cached_fetch, humanize_choice, humanize_date
from care_im_wrapper.data.common import resolve_target_patient
from care_im_wrapper.data.exceptions import NoDataError
from care_im_wrapper.data.records import ProcedureRecord
from care_im_wrapper.models import ConversationSession
from care_im_wrapper.settings import plugin_settings


@cached_fetch(timeout_seconds=int(plugin_settings.DATA_CACHE_TIMEOUT_SECONDS))
def fetch_procedures(actor: Actor, session: ConversationSession) -> list[ProcedureRecord]:
    """
    patient: returns their own last 10 procedures.
    staff:   returns procedures for session.active_patient_external_id.
    Raises PermissionDeniedError, NoDataError, MissingContextError.
    """
    from care.emr.models.service_request import ServiceRequest  # type: ignore[import-untyped]

    patient = resolve_target_patient(actor, session)
    queryset = ServiceRequest.objects.filter(patient=patient)
    records = queryset.order_by("-created_date")[:10]
    if not records:
        raise NoDataError

    procedure_records = []
    for sr in records:
        status = humanize_choice(getattr(sr, "status", None))
        date = humanize_date(getattr(sr, "created_date", None))
        name = _extract_service_name(sr)
        procedure_records.append(ProcedureRecord(name=name, date=date, status=status))

    return procedure_records


def _extract_service_name(sr) -> str:
    """
    Extracts a human-readable service name from ServiceRequest.
    """
    code = getattr(sr, "code", None)
    if isinstance(code, dict):
        return code.get("display") or code.get("text") or "Unspecified procedure"
    return str(code) if code else "Unspecified procedure"
