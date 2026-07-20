"""Generate-or-fetch service for signed-URL PDF documents.

The only module that imports core's report/file generation internals; everything
downstream sees a ``DocumentLink``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from care_im_wrapper.data.common import authorize_patient_access
from care_im_wrapper.documents.exceptions import DocumentUnavailableError
from care_im_wrapper.models import DocumentLink, DocumentLinkObjectKind
from care_im_wrapper.settings import plugin_settings

if TYPE_CHECKING:
    from care.emr.models.diagnostic_report import DiagnosticReport  # pyright: ignore[reportMissingImports]
    from care.emr.models.encounter import Encounter  # pyright: ignore[reportMissingImports]
    from care.emr.models.patient import Patient  # pyright: ignore[reportMissingImports]

    from care_im_wrapper.auth.actor import Actor

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DocumentRequest:
    """Describes which document a flow wants a link for.

    ``encounter`` resolves the Template and is the associating_id for generation.
    ``diagnostic_report``, when set, is checked first for an uploaded FileUpload; if one
    exists the link references it and nothing is generated.
    """

    document_type: str
    encounter: Encounter
    diagnostic_report: DiagnosticReport | None = None


def _find_uploaded_diagnostic_file(diagnostic_report: DiagnosticReport) -> Any | None:
    from care.emr.models.file_upload import FileUpload  # pyright: ignore[reportMissingImports]

    return (
        FileUpload.objects.filter(
            file_type="diagnostic_report",
            associating_id=str(diagnostic_report.external_id),
            upload_completed=True,
            is_archived=False,
        )
        .order_by("-created_date")
        .first()
    )


def _resolve_encounter_template(encounter: Encounter) -> Any:
    from care.emr.models.report.template import Template  # pyright: ignore[reportMissingImports]

    template = (
        Template.objects.filter(context="encounter_base", status="active", facility=encounter.facility).first()
        or Template.objects.filter(context="encounter_base", status="active", facility=None).first()
    )
    if template is None:
        raise DocumentUnavailableError(
            f"No active 'encounter_base' Template configured for facility={encounter.facility_id} or globally."
        )
    return template


def _generate_encounter_report(encounter: Encounter) -> Any:
    from care.emr.reports.report_utils import generate_and_upload_report  # pyright: ignore[reportMissingImports]

    template = _resolve_encounter_template(encounter)
    try:
        return generate_and_upload_report(
            template=template,
            report_type="encounter_report",
            associating_id=str(encounter.external_id),
            output_format=template.default_format,
        )
    except Exception as exc:
        logger.exception(
            "get_or_create_document_link: report generation failed for encounter=%s", encounter.external_id
        )
        raise DocumentUnavailableError("Report generation failed.") from exc


def _reuse_existing_link(patient: Patient, object_kind: str, object_external_id: Any) -> DocumentLink | None:
    candidate = (
        DocumentLink.objects.filter(
            patient_external_id=patient.external_id,
            object_kind=object_kind,
            object_external_id=object_external_id,
        )
        .order_by("-created_date")
        .first()
    )
    if candidate is not None and candidate.is_valid():
        return candidate
    return None


def _issue_link(
    patient: Patient,
    object_kind: str,
    object_external_id: Any,
    document_type: str,
    provider: str,
) -> DocumentLink:
    """Reuse a still-valid link for the same (patient, object) or mint a new one."""
    existing = _reuse_existing_link(patient, object_kind, object_external_id)
    if existing is not None:
        return existing

    link = DocumentLink.objects.create(
        object_kind=object_kind,
        object_external_id=object_external_id,
        document_type=document_type,
        patient_external_id=patient.external_id,
        provider=provider,
        expires_at=timezone.now() + timedelta(seconds=int(plugin_settings.DOCUMENT_LINK_TTL_SECONDS)),
    )
    logger.info(
        "DocumentLink issued: document_type=%s object_kind=%s object_external_id=%s patient=%s",
        document_type,
        object_kind,
        object_external_id,
        patient.external_id,
    )
    return link


def _locate_or_generate_document_link(
    patient: Patient,
    document_request: DocumentRequest,
    provider: str,
) -> DocumentLink:
    file_upload = None
    if document_request.diagnostic_report is not None:
        file_upload = _find_uploaded_diagnostic_file(document_request.diagnostic_report)

    if file_upload is not None:
        object_kind = DocumentLinkObjectKind.FILE_UPLOAD
        object_external_id = file_upload.external_id
    else:
        report_upload = _generate_encounter_report(document_request.encounter)
        object_kind = DocumentLinkObjectKind.REPORT_UPLOAD
        object_external_id = report_upload.external_id

    return _issue_link(
        patient=patient,
        object_kind=object_kind,
        object_external_id=object_external_id,
        document_type=document_request.document_type,
        provider=provider,
    )


def get_or_create_document_link(
    actor: Actor,
    patient: Patient,
    document_request: DocumentRequest,
    provider: str,
) -> DocumentLink:
    """Pull path: uploaded file first, else encounter-scoped generation, then issue or
    reuse a patient-scoped DocumentLink.

    Raises PermissionDeniedError if actor/patient fail the identity/RBAC check, and
    DocumentUnavailableError if no document could be located or generated.
    """
    authorize_patient_access(actor, patient)
    return _locate_or_generate_document_link(patient, document_request, provider)


def get_system_document_link(
    patient: Patient,
    document_request: DocumentRequest,
    provider: str,
) -> DocumentLink:
    """Push path. No actor to authorize against -- the system is minting a capability for
    the patient the record already belongs to, not answering a read request.

    Raises DocumentUnavailableError if no document could be located or generated.
    """
    return _locate_or_generate_document_link(patient, document_request, provider)


def build_document_url(link: DocumentLink) -> str:
    """Absolute public URL for the token redirect route.

    DOCUMENT_LINK_BASE_URL is the public origin only; the path comes from the URLconf.
    Falls back to BACKEND_DOMAIN rather than emitting a bare path -- this URL goes straight
    to a messaging provider, where a relative one is unusable. Never a presigned URL.
    """
    origin = str(plugin_settings.DOCUMENT_LINK_BASE_URL).strip() or str(settings.BACKEND_DOMAIN)
    if "://" not in origin:
        # BACKEND_DOMAIN carries no scheme, and loopback is only ever served over http.
        host = origin.split(":", 1)[0]
        origin = f"{'http' if host in ('localhost', '127.0.0.1') else 'https'}://{origin}"
    return f"{origin.rstrip('/')}{reverse('im-wrapper-document-redirect', args=[link.token])}"
