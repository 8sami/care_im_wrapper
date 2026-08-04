"""Fetch diagnostic report details for the authenticated actor."""

from care_im_wrapper.auth.actor import Actor
from care_im_wrapper.data.base import cached_fetch, humanize_choice, humanize_date
from care_im_wrapper.data.common import resolve_target_encounter
from care_im_wrapper.data.pagination import Page, map_page, paginate_or_raise, scan_bound
from care_im_wrapper.data.records import LabReportRecord
from care_im_wrapper.models import ConversationSession
from care_im_wrapper.settings import plugin_settings

DEDUPE_SCAN_FACTOR = 5


@cached_fetch(timeout_seconds=int(plugin_settings.DATA_CACHE_TIMEOUT_SECONDS))
def fetch_lab_reports(actor: Actor, session: ConversationSession) -> Page:
    """One page of the open encounter's lab reports, newest first.

    Scoped to the encounter, as care_fe's `diagnostic_reports` tab is.
    """
    from care.emr.models.diagnostic_report import DiagnosticReport  # type: ignore[import-untyped]
    from care.emr.resources.diagnostic_report.spec import DiagnosticReportStatusChoices  # type: ignore[import-untyped]

    encounter = resolve_target_encounter(actor, session)
    all_records = DiagnosticReport.objects.filter(patient=encounter.patient, encounter=encounter).order_by(
        "-created_date"
    )[: scan_bound(session, DEDUPE_SCAN_FACTOR)]

    latest_by_group: dict[str, DiagnosticReport] = {}
    for r in all_records:
        group_key = str(r.service_request_id) if r.service_request_id else f"report:{r.id}"
        if group_key not in latest_by_group:
            latest_by_group[group_key] = r

    page = paginate_or_raise(list(latest_by_group.values()), session)

    final_status = DiagnosticReportStatusChoices.final.value

    def build(report) -> LabReportRecord:
        return LabReportRecord(
            name=_extract_report_name(report),
            date=humanize_date(getattr(report, "created_date", None)),
            status=humanize_choice(getattr(report, "status", None)),
            external_id=str(report.external_id) if report.status == final_status else "",
        )

    return map_page(page, build)


def _extract_report_name(report) -> str:
    code = getattr(report, "code", None)
    if isinstance(code, dict):
        return code.get("text") or code.get("display") or "Lab report"
    return str(code) if code else "Lab report"
