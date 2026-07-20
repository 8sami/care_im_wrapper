"""Resolves which object a selected record's DocumentRequest targets.

Returning None means no document is available for the record; callers report that to the
user rather than treating it as an error.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from care_im_wrapper.documents.service import DocumentRequest

if TYPE_CHECKING:
    from care.emr.models.patient import Patient  # pyright: ignore[reportMissingImports]


def resolve_diagnostic_report_document(patient: Patient, external_id: str) -> DocumentRequest | None:
    """The DiagnosticReport the user picked off the lab-reports list.

    Scoped to `patient` so a stale or guessed external_id cannot reach another patient's
    record. Reports sharing an encounter with no uploaded file of their own resolve to the
    same encounter PDF -- core has no per-test report type.
    """
    from care.emr.models.diagnostic_report import DiagnosticReport  # pyright: ignore[reportMissingImports]

    report = (
        DiagnosticReport.objects.filter(patient=patient, external_id=external_id).select_related("encounter").first()
    )
    if report is None:
        return None
    return DocumentRequest(document_type="diagnostic_report", encounter=report.encounter, diagnostic_report=report)
