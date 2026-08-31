"""Resolves which object a selected record's DocumentRequest targets.

Returning None means no document is available for the record; callers report that to the
user rather than treating it as an error.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from care_im_wrapper.documents.service import (
    DIAGNOSTIC_REPORT_DOCUMENT_TYPE,
    DISCHARGE_SUMMARY_DOCUMENT_TYPE,
    DocumentRequest,
)

if TYPE_CHECKING:
    from care.emr.models.patient import Patient  # pyright: ignore[reportMissingImports]

# core's ReportTypeRegistry key (care/emr/reports/report_types.py) for a discharge summary,
# the same one Care's Reports tab generates against.
_DISCHARGE_SUMMARY_REPORT_TYPE = "discharge_summary"


def resolve_diagnostic_report_document(patient: Patient, external_id: str) -> DocumentRequest | None:
    """The DiagnosticReport the user picked off the lab-reports list.

    Scoped to `patient` so a stale or guessed external_id cannot reach another patient's
    record. A report with no uploaded file of its own is unavailable; there is no
    encounter-PDF fallback.
    """
    from care.emr.models.diagnostic_report import DiagnosticReport  # pyright: ignore[reportMissingImports]

    report = (
        DiagnosticReport.objects.filter(patient=patient, external_id=external_id).select_related("encounter").first()
    )
    if report is None:
        return None
    return DocumentRequest(
        document_type=DIAGNOSTIC_REPORT_DOCUMENT_TYPE, encounter=report.encounter, diagnostic_report=report
    )


def resolve_encounter_document(patient: Patient, external_id: str) -> DocumentRequest | None:
    """The encounter the user picked off the encounter-details list, as its latest
    staff-generated discharge summary (Care's Reports tab).

    Scoped to `patient` so a stale or guessed external_id cannot reach another patient's
    encounter. Never generated here: an encounter staff have not generated one for yet has
    no discharge summary to share, and a WhatsApp request is not a reason to render one.
    """
    from care.emr.models.encounter import Encounter  # pyright: ignore[reportMissingImports]
    from care.emr.models.report.report_upload import ReportUpload  # pyright: ignore[reportMissingImports]

    encounter = Encounter.objects.filter(patient=patient, external_id=external_id).first()
    if encounter is None:
        return None

    report_upload = (
        ReportUpload.objects.filter(
            report_type=_DISCHARGE_SUMMARY_REPORT_TYPE,
            associating_id=str(encounter.external_id),
            upload_completed=True,
            is_archived=False,
        )
        .order_by("-created_date")
        .first()
    )
    if report_upload is None:
        return None

    return DocumentRequest(
        document_type=DISCHARGE_SUMMARY_DOCUMENT_TYPE,
        encounter=encounter,
        report_upload=report_upload,
    )
