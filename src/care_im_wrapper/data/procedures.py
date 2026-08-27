"""Fetch procedure/service request details for the authenticated actor."""

from care_im_wrapper.auth.actor import Actor
from care_im_wrapper.data.base import ENTERED_IN_ERROR_STATUS, cached_fetch, humanize_choice, humanize_date
from care_im_wrapper.data.common import resolve_target_encounter
from care_im_wrapper.data.pagination import Page, map_page, paginate_or_raise
from care_im_wrapper.data.records import ProcedureRecord
from care_im_wrapper.models import ConversationSession
from care_im_wrapper.settings import plugin_settings


@cached_fetch(timeout_seconds=int(plugin_settings.DATA_CACHE_TIMEOUT_SECONDS))
def fetch_procedures(actor: Actor, session: ConversationSession) -> Page:
    """One page of the open encounter's procedures, newest first.

    Scoped to the encounter, as care_fe's `service_requests` tab is. Unlike the other
    encounter-scoped models `ServiceRequest.encounter` is nullable, so a request recorded
    without one is reachable from no encounter at all -- also true of care_fe's tab, which
    queries by encounter id.
    """
    from care.emr.models.service_request import ServiceRequest  # type: ignore[import-untyped]

    encounter = resolve_target_encounter(actor, session)
    queryset = (
        ServiceRequest.objects.filter(patient=encounter.patient, encounter=encounter)
        .exclude(status=ENTERED_IN_ERROR_STATUS)
        .select_related("activity_definition")
    )
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
    activity_definition = getattr(sr, "activity_definition", None)
    code = getattr(sr, "code", None)
    if isinstance(code, dict):
        coded_name = code.get("display") or code.get("text")
    else:
        coded_name = str(code) if code else None
    return (
        getattr(sr, "title", None)
        or getattr(activity_definition, "title", None)
        or coded_name
        or "Unspecified procedure"
    )
