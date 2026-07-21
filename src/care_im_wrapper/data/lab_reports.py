"""Fetch diagnostic report details for the authenticated actor."""

from care_im_wrapper.auth.actor import Actor
from care_im_wrapper.data.base import cached_fetch, humanize_choice, humanize_date
from care_im_wrapper.data.common import resolve_target_patient
from care_im_wrapper.data.exceptions import NoDataError
from care_im_wrapper.data.records import LabReportRecord
from care_im_wrapper.models import ConversationSession
from care_im_wrapper.settings import plugin_settings

# Rows scanned per row displayed, when collapsing reports to one per service request.
# Bounds the query; a patient whose newest DATA_FETCH_LIMIT * this many reports all share
# fewer than DATA_FETCH_LIMIT service requests simply sees fewer rows.
DEDUPE_SCAN_FACTOR = 5


@cached_fetch(timeout_seconds=int(plugin_settings.DATA_CACHE_TIMEOUT_SECONDS))
def fetch_lab_reports(actor: Actor, session: ConversationSession) -> list[LabReportRecord]:
    """
    patient: returns their own last 10 lab reports.
    staff:   returns lab reports for session.active_patient_external_id.
    Raises PermissionDeniedError, NoDataError, MissingContextError.
    """
    from care.emr.models.diagnostic_report import DiagnosticReport  # type: ignore[import-untyped]

    patient = resolve_target_patient(actor, session)
    limit = int(plugin_settings.DATA_FETCH_LIMIT)
    # Newest report per service request. Bounded scan -- a long history is thousands of rows
    # and this runs on the inbound-message path.
    all_records = DiagnosticReport.objects.filter(patient=patient).order_by("-created_date")[
        : limit * DEDUPE_SCAN_FACTOR
    ]

    latest_by_group: dict[str, DiagnosticReport] = {}
    for r in all_records:
        # service_request_id is None (not absent) on a standalone report, so a getattr default
        # would collapse them all into one group -- use a per-row key instead.
        group_key = str(r.service_request_id) if r.service_request_id else f"report:{r.id}"
        if group_key not in latest_by_group:
            latest_by_group[group_key] = r

    records = list(latest_by_group.values())[:limit]
    if not records:
        raise NoDataError

    lab_report_records = []
    for report in records:
        status = humanize_choice(getattr(report, "status", None))
        date = humanize_date(getattr(report, "created_date", None))
        name = _extract_report_name(report)
        lab_report_records.append(
            LabReportRecord(name=name, date=date, status=status, external_id=str(report.external_id))
        )

    return lab_report_records


def _extract_report_name(report) -> str:
    code = getattr(report, "code", None)
    if isinstance(code, dict):
        return code.get("text") or code.get("display") or "Lab report"
    return str(code) if code else "Lab report"
