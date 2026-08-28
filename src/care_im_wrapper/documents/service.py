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
from care_im_wrapper.data.exceptions import PermissionDeniedError
from care_im_wrapper.documents import kinds
from care_im_wrapper.documents.exceptions import DocumentUnavailableError
from care_im_wrapper.models import ConversationSession, DocumentLink, DocumentLinkObjectKind
from care_im_wrapper.settings import plugin_settings

if TYPE_CHECKING:
    from care.emr.models.diagnostic_report import DiagnosticReport  # pyright: ignore[reportMissingImports]
    from care.emr.models.encounter import Encounter  # pyright: ignore[reportMissingImports]
    from care.emr.models.patient import Patient  # pyright: ignore[reportMissingImports]

    from care_im_wrapper.auth.actor import Actor

logger = logging.getLogger(__name__)

# core's ReportTypeRegistry keys (care/emr/reports/report_types.py) and the Template.context
# their Templates are authored against. Encounter-associated reports share encounter_base.
ENCOUNTER_REPORT_TYPE = "encounter_report"
DISCHARGE_SUMMARY_REPORT_TYPE = "discharge_summary"
ENCOUNTER_TEMPLATE_CONTEXT = "encounter_base"

# DocumentRequest.document_type labels (display/audit only, not core registry keys).
DIAGNOSTIC_REPORT_DOCUMENT_TYPE = "diagnostic_report"
DISCHARGE_SUMMARY_DOCUMENT_TYPE = "discharge_summary"


@dataclass(frozen=True)
class DocumentRequest:
    """Describes which document a flow wants a link for.

    ``encounter`` resolves the Template and is the associating_id for generation.
    ``diagnostic_report``, when set, makes the link address that report directly for
    rendering; that path never generates.
    """

    document_type: str
    encounter: Encounter
    # core ReportTypeRegistry key used for the generate path: gates staff authorization
    # (write_report_authorizer) and tags the ReportUpload. Ignored whenever
    # diagnostic_report is set, since that path never generates.
    report_type: str = ENCOUNTER_REPORT_TYPE
    diagnostic_report: DiagnosticReport | None = None


def _resolve_encounter_template(encounter: Encounter) -> Any:
    from care.emr.models.report.template import Template  # pyright: ignore[reportMissingImports]

    template = (
        Template.objects.filter(
            context=ENCOUNTER_TEMPLATE_CONTEXT, status="active", facility=encounter.facility
        ).first()
        or Template.objects.filter(context=ENCOUNTER_TEMPLATE_CONTEXT, status="active", facility=None).first()
    )
    if template is None:
        raise DocumentUnavailableError(
            f"No active '{ENCOUNTER_TEMPLATE_CONTEXT}' Template configured for "
            f"facility={encounter.facility_id} or globally."
        )
    return template


def _find_existing_encounter_report(encounter: Encounter, report_type: str) -> Any | None:
    """A recently generated report of this type for this encounter, if one exists.

    Keyed on report_type too, so a discharge summary is never reused for an encounter report.
    Bounded by ENCOUNTER_REPORT_REUSE_SECONDS (an encounter report is a clinical snapshot, so
    this only collapses repeat taps, it is not a cache).
    """
    from care.emr.models.report.report_upload import ReportUpload  # pyright: ignore[reportMissingImports]

    cutoff = timezone.now() - timedelta(seconds=int(plugin_settings.ENCOUNTER_REPORT_REUSE_SECONDS))
    return (
        ReportUpload.objects.filter(
            report_type=report_type,
            associating_id=str(encounter.external_id),
            upload_completed=True,
            is_archived=False,
            created_date__gte=cutoff,
        )
        .order_by("-created_date")
        .first()
    )


def _generate_encounter_report(encounter: Encounter, report_type: str) -> Any:
    from care.emr.reports.report_utils import generate_and_upload_report  # pyright: ignore[reportMissingImports]

    template = _resolve_encounter_template(encounter)
    try:
        return generate_and_upload_report(
            template=template,
            report_type=report_type,
            associating_id=str(encounter.external_id),
            output_format=template.default_format,
        )
    except Exception as exc:
        logger.exception(
            "get_or_create_document_link: report generation failed for encounter=%s report_type=%s",
            encounter.external_id,
            report_type,
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


def _authorize_report_generation(actor: Actor, patient: Patient, document_request: DocumentRequest) -> None:
    """Authorization for generating (as opposed to referencing an uploaded file) a report.

    Patient actor: identity only -- core's report authorizers are user-based and a patient
    actor's instance is a Patient, not a User, so its own-record scope is the ceiling.
    Staff actor: exactly what core's HTTP generate endpoint enforces
    (care/emr/api/viewsets/report/report_upload.py) -- the report-type's own authorizer via
    write_report_authorizer, not the coarser can_view_patient_obj. Keeps the IM channel from
    being a weaker door to the same generated document.

    Raises PermissionDeniedError on failure; DocumentUnavailableError if the associating
    object the authorizer looks up no longer exists.
    """
    from care.emr.reports.authorizers.utils import write_report_authorizer  # pyright: ignore[reportMissingImports]
    from django.http import Http404
    from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied

    if actor.user_type == ConversationSession.UserType.PATIENT.value:
        if patient.id != actor.instance.id:
            raise PermissionDeniedError
        return

    associating_id = str(document_request.encounter.external_id)
    try:
        write_report_authorizer(actor.instance, document_request.report_type, associating_id)
    except DRFPermissionDenied as exc:
        raise PermissionDeniedError from exc
    except Http404 as exc:
        raise DocumentUnavailableError("Report subject not found.") from exc


def _locate_or_generate_document_link(
    actor: Actor | None,
    patient: Patient,
    document_request: DocumentRequest,
    provider: str,
) -> DocumentLink:
    if document_request.diagnostic_report is not None:
        # The link addresses the report itself, not any file: the public page renders it
        # the way care_fe's print view does, with uploaded files shown as attachments
        # inside it. So a report with no attachment is still deliverable, and one with
        # several no longer loses all but the newest. There is no encounter-report
        # fallback -- that delivered a document about a different subject entirely.
        # Core has no report authorizer for a diagnostic report, so the patient view
        # scope is the applicable check.
        if actor is not None:
            authorize_patient_access(actor, patient)
        object_kind = DocumentLinkObjectKind.DIAGNOSTIC_REPORT
        object_external_id = document_request.diagnostic_report.external_id
    else:
        # actor is None only on the system push path, which mints for the patient the record
        # already belongs to and has no user to authorize.
        if actor is not None:
            _authorize_report_generation(actor, patient, document_request)
        # Reuse before generating: each generate call mints a new external_id, so _issue_link's
        # reuse check can't dedupe a fresh render -- without this every request re-rendered.
        report_upload = _find_existing_encounter_report(
            document_request.encounter, document_request.report_type
        ) or _generate_encounter_report(document_request.encounter, document_request.report_type)
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
    """Pull path: a lab report addressed for rendering, or encounter-scoped generation,
    then issue or reuse a patient-scoped DocumentLink. Authorization is per-branch (see
    _locate_or_generate_document_link): a referenced uploaded file uses the patient view
    scope, a generated report matches core's report-generation authorizer.

    Raises PermissionDeniedError if actor/patient fail the RBAC check, and
    DocumentUnavailableError if no document could be located or generated.
    """
    return _locate_or_generate_document_link(actor, patient, document_request, provider)


def get_system_document_link(
    patient: Patient,
    document_request: DocumentRequest,
    provider: str,
) -> DocumentLink:
    """Push path. No actor to authorize against -- the system is minting a capability for
    the patient the record already belongs to, not answering a read request.

    Raises DocumentUnavailableError if no document could be located or generated.
    """
    return _locate_or_generate_document_link(None, patient, document_request, provider)


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
    """The URL to send the patient for this document.

    A rendered document (a lab report) goes to the care_fe page, which reads the record
    through the public payload endpoint and draws CARE's own print view. A stored file
    keeps going to the backend redirect, which mints a presign per hit. Never a presigned
    URL itself -- the token is the durable capability.
    """
    kind = kinds.get(link.document_type)
    if kind is not None and kind.mode == kinds.RENDER:
        origin = _absolute_origin(plugin_settings.DOCUMENT_PAGE_BASE_URL, settings.CURRENT_DOMAIN)
        return f"{origin}/public/documents/{link.token}"

    origin = _absolute_origin(plugin_settings.DOCUMENT_LINK_BASE_URL, settings.BACKEND_DOMAIN)
    return f"{origin}{reverse('im-wrapper-document-redirect', args=[link.token])}"
