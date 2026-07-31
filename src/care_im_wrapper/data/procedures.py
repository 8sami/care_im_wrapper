"""Fetch procedure/service request details for the authenticated actor."""

from care_im_wrapper.auth.actor import Actor
from care_im_wrapper.data.base import ENTERED_IN_ERROR_STATUS, cached_fetch, humanize_choice, humanize_date
from care_im_wrapper.data.common import resolve_target_patient
from care_im_wrapper.data.pagination import Page, map_page, paginate_or_raise
from care_im_wrapper.data.records import ProcedureRecord
from care_im_wrapper.models import ConversationSession
from care_im_wrapper.settings import plugin_settings


@cached_fetch(timeout_seconds=int(plugin_settings.DATA_CACHE_TIMEOUT_SECONDS))
def fetch_procedures(actor: Actor, session: ConversationSession) -> Page:
    """patient: returns one page of their own procedures, newest first."""
    from care.emr.models.service_request import ServiceRequest  # type: ignore[import-untyped]

    patient = resolve_target_patient(actor, session)
    queryset = ServiceRequest.objects.filter(patient=patient).exclude(status=ENTERED_IN_ERROR_STATUS)
    page = paginate_or_raise(queryset.order_by("-created_date"), session)

    def build(sr) -> ProcedureRecord:
        return ProcedureRecord(
            name=_extract_service_name(sr),
            date=humanize_date(getattr(sr, "created_date", None)),
            status=humanize_choice(getattr(sr, "status", None)),
        )

    return map_page(page, build)


def _extract_service_name(sr) -> str:
    """
    Extracts a human-readable service name from ServiceRequest.
    """
    code = getattr(sr, "code", None)
    if isinstance(code, dict):
        return code.get("display") or code.get("text") or "Unspecified procedure"
    return str(code) if code else "Unspecified procedure"
