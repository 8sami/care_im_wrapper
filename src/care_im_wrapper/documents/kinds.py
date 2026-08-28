"""The kinds of document a DocumentLink can address, and how each is served publicly.

Adding a document type is one ``register()`` call here plus a renderer keyed on the same
slug in the plugin frontend. Nothing else in the pull, push, or public-view paths changes.

Two modes exist because CARE itself draws the line:

* ``RENDER`` -- care_fe owns a print view for this document and no PDF exists anywhere
  (the browser makes one when staff hit Print). The link carries the *subject* and the
  public endpoint returns its data, so the page can draw the same view for a patient.
  ``template_slug`` is the facility print_templates key care_fe uses for that view, so
  the patient's page gets the facility's configured letterhead.
* ``FILE`` -- the document is a pre-rendered artifact in object storage (a template-builder
  report, or a file a lab uploaded). The link carries that artifact and the page displays it.

Slugs for RENDER kinds mirror care_fe's ``PrintTemplateType``; a FILE kind has no print
view and so no template slug.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from care_im_wrapper.documents.exceptions import DocumentUnavailableError
from care_im_wrapper.models import DocumentLinkObjectKind

if TYPE_CHECKING:
    from care_im_wrapper.models import DocumentLink

RENDER = "render"
FILE = "file"


@dataclass(frozen=True)
class DocumentKind:
    """How one document type is located and handed to the public page.

    ``build`` receives the link and returns the JSON body for that document, without the
    envelope fields (kind/mode) the view adds. It raises DocumentUnavailableError if the
    subject has gone away since the link was minted.
    """

    slug: str
    mode: str
    object_kind: str
    build: Callable[[DocumentLink], dict[str, Any]]
    template_slug: str = ""


_REGISTRY: dict[str, DocumentKind] = {}


def register(kind: DocumentKind) -> None:
    if kind.slug in _REGISTRY:
        msg = f"Document kind '{kind.slug}' is already registered"
        raise ValueError(msg)
    _REGISTRY[kind.slug] = kind


def get(slug: str) -> DocumentKind | None:
    return _REGISTRY.get(slug)


def _facility_payload(facility: Any) -> dict[str, Any]:
    """Only what care_fe's print layout reads off a facility.

    Deliberately not FacilityRetrieveSpec: that carries a permissions mixin that needs a
    user, and this endpoint has none. Four fields is also all an anonymous holder of the
    link should learn about the facility.
    """
    if facility is None:
        return {}
    return {
        "name": facility.name,
        "address": facility.address,
        "phone_number": facility.phone_number,
        "print_templates": facility.print_templates or [],
    }


def _attachment_payloads(file_type: str, associating_id: str) -> list[dict[str, Any]]:
    """Every completed, unarchived attachment, oldest first, each with a short-TTL URL.

    Every one, not just the newest: care_fe's print view renders them all, and a report
    whose attachments are silently dropped is a different document from the one staff see.
    """
    from care.emr.models.file_upload import FileUpload  # pyright: ignore[reportMissingImports]

    from care_im_wrapper.settings import plugin_settings

    files = FileUpload.objects.filter(
        file_type=file_type,
        associating_id=associating_id,
        upload_completed=True,
        is_archived=False,
    ).order_by("created_date")

    payloads = []
    for file_upload in files:
        payloads.append(
            {
                "id": str(file_upload.external_id),
                "name": file_upload.name,
                "extension": file_upload.get_extension(),
                "url": file_upload.files_manager.read_signed_url(
                    file_upload, duration=int(plugin_settings.DOCUMENT_PRESIGN_TTL_SECONDS)
                ),
            }
        )
    return payloads


def _build_diagnostic_report(link: DocumentLink) -> dict[str, Any]:
    """The same body care_fe's authenticated retrieve endpoint returns, plus attachments.

    Reusing core's own read spec is the point: the public page renders a copy of care_fe's
    print view, so it must receive the shape that view already consumes. Hand-rolling a
    parallel payload here would be a second thing to keep in step with core.
    """
    from care.emr.models.diagnostic_report import DiagnosticReport  # pyright: ignore[reportMissingImports]
    from care.emr.resources.diagnostic_report.spec import (  # pyright: ignore[reportMissingImports]
        DiagnosticReportRetrieveSpec,
    )

    report = (
        DiagnosticReport.objects.filter(external_id=link.object_external_id)
        .select_related("encounter", "encounter__patient", "facility", "service_request")
        .first()
    )
    if report is None:
        raise DocumentUnavailableError("Diagnostic report no longer exists.")

    facility = report.facility or report.encounter.facility
    return {
        "report": DiagnosticReportRetrieveSpec.serialize(report).to_json(),
        "files": _attachment_payloads("diagnostic_report", str(report.external_id)),
        "facility": _facility_payload(facility),
    }


def _build_stored_file(link: DocumentLink) -> dict[str, Any]:
    """A pre-rendered artifact: the page needs a URL and nothing else."""
    try:
        return {"file": {"url": link.mint_read_url()}}
    except Exception as exc:
        raise DocumentUnavailableError("Stored document could not be read.") from exc


DIAGNOSTIC_REPORT = DocumentKind(
    slug="diagnostic_report",
    mode=RENDER,
    object_kind=DocumentLinkObjectKind.DIAGNOSTIC_REPORT,
    build=_build_diagnostic_report,
    # matches care_fe PrintTemplateType.diagnostic_report
    template_slug="diagnostic_report",
)

DISCHARGE_SUMMARY = DocumentKind(
    slug="discharge_summary",
    mode=FILE,
    object_kind=DocumentLinkObjectKind.REPORT_UPLOAD,
    build=_build_stored_file,
)

register(DIAGNOSTIC_REPORT)
register(DISCHARGE_SUMMARY)
