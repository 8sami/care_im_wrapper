"""Locate-or-reuse service for signed-URL documents.

Every document this service links to already exists -- a finalised DiagnosticReport, or a
report staff generated ahead of time (Care's Reports tab) -- and is never generated on the
fly here. Everything downstream sees a ``DocumentLink``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.utils import timezone

from care_im_wrapper.data.common import authorize_patient_access
from care_im_wrapper.documents.exceptions import DocumentUnavailableError
from care_im_wrapper.models import DocumentLink, DocumentLinkObjectKind
from care_im_wrapper.settings import plugin_settings

if TYPE_CHECKING:
    from care.emr.models.diagnostic_report import DiagnosticReport  # pyright: ignore[reportMissingImports]
    from care.emr.models.encounter import Encounter  # pyright: ignore[reportMissingImports]
    from care.emr.models.patient import Patient  # pyright: ignore[reportMissingImports]
    from care.emr.models.report.report_upload import ReportUpload  # pyright: ignore[reportMissingImports]

    from care_im_wrapper.auth.actor import Actor

logger = logging.getLogger(__name__)

# DocumentRequest.document_type labels (display/audit only, not core registry keys).
DIAGNOSTIC_REPORT_DOCUMENT_TYPE = "diagnostic_report"
DISCHARGE_SUMMARY_DOCUMENT_TYPE = "discharge_summary"


@dataclass(frozen=True)
class DocumentRequest:
    """Describes which document a flow wants a link for.

    ``encounter`` is the clinical record the document belongs to (audit/display only; it
    does not drive resolution). Exactly one of ``diagnostic_report`` or ``report_upload``
    must be set -- it is the artifact the link addresses directly.
    """

    document_type: str
    encounter: Encounter
    diagnostic_report: DiagnosticReport | None = None
    report_upload: ReportUpload | None = None


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


def _locate_document_link(
    actor: Actor | None,
    patient: Patient,
    document_request: DocumentRequest,
    provider: str,
) -> DocumentLink:
    """Resolves the artifact a DocumentRequest addresses and issues or reuses a link to it.

    Core has no separate authorizer for reading a diagnostic report or an already generated
    report, so the patient view scope applies to both.
    """
    if document_request.diagnostic_report is not None:
        # The link addresses the report itself, not any file: the public page renders it
        # the way care_fe's print view does, with uploaded files shown as attachments
        # inside it. So a report with no attachment is still deliverable, and one with
        # several no longer loses all but the newest.
        object_kind = DocumentLinkObjectKind.DIAGNOSTIC_REPORT
        object_external_id = document_request.diagnostic_report.external_id
    elif document_request.report_upload is not None:
        # A report staff already generated via Care's Reports tab. The link addresses it
        # directly.
        object_kind = DocumentLinkObjectKind.REPORT_UPLOAD
        object_external_id = document_request.report_upload.external_id
    else:
        msg = (
            f"DocumentRequest for document_type={document_request.document_type!r} "
            "addresses neither a report nor a file."
        )
        raise DocumentUnavailableError(msg)

    # actor is None only on the system push path, which mints for the patient the record
    # already belongs to and has no user to authorize.
    if actor is not None:
        authorize_patient_access(actor, patient)

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
    """Pull path: a lab report or an already-stored file, addressed directly, then issue or
    reuse a patient-scoped DocumentLink.

    Raises PermissionDeniedError if actor/patient fail the RBAC check, and
    DocumentUnavailableError if no such document exists.
    """
    return _locate_document_link(actor, patient, document_request, provider)


def get_system_document_link(
    patient: Patient,
    document_request: DocumentRequest,
    provider: str,
) -> DocumentLink:
    """Push path. No actor to authorize against -- the system is minting a capability for
    the patient the record already belongs to, not answering a read request.

    Raises DocumentUnavailableError if no such document exists.
    """
    return _locate_document_link(None, patient, document_request, provider)


def _absolute_origin(configured: str, fallback: str) -> str:
    """A scheme-qualified origin. These URLs go straight to a messaging provider, where a
    relative one is unusable, so never emit a bare path."""
    origin = str(configured).strip() or str(fallback)
    if "://" not in origin:
        # The CARE domain settings carry no scheme, and loopback is only ever served over http.
        host = origin.split(":", 1)[0]
        origin = f"{'http' if host in ('localhost', '127.0.0.1') else 'https'}://{origin}"
    return origin.rstrip("/")


def build_document_url(link: DocumentLink) -> str:
    """The URL to send the patient, for any kind of document.

    Always the care_fe page: one address to configure in a provider's message template
    and one to get wrong, and a document type added later needs no change here. The page
    reads the record through the public payload endpoint and either draws CARE's print
    view or hands the browser the stored file.

    Never a presigned URL itself -- the token is the durable capability, and presigns are
    minted per request behind it.
    """
    origin = _absolute_origin(plugin_settings.DOCUMENT_PAGE_BASE_URL, settings.CURRENT_DOMAIN)
    return f"{origin}/public/documents/{link.token}"
