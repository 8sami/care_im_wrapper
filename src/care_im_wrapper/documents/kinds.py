"""The kinds of document a DocumentLink can address, and how each is served publicly.

Adding a document type is one ``register()`` call here plus a renderer keyed on the same
slug in the plugin frontend. Nothing else in the pull, push, or public-view paths changes.

Two modes exist because CARE itself draws the line:

* ``RENDER`` -- care_fe has a print view and no PDF exists anywhere; the browser makes
  one when staff hit Print. The link carries the subject, and the page draws it from the
  data. ``template_slug`` is the facility print_templates key care_fe uses for that view,
  so the patient sees the same letterhead. Slugs mirror care_fe's ``PrintTemplateType``.
* ``FILE`` -- a pre-rendered artifact in object storage (a template-builder report, or a
  file a lab uploaded). No print view, so no template slug.
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

    Deliberately not FacilityRetrieveSpec: that carries a permissions mixin needing a
    user, and it would tell an anonymous link holder far more than a letterhead does.
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
    missing its attachments is a different document from the one staff see.
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


def _resolve_condition_tag_displays(observations: list[dict[str, Any]]) -> None:
    """Annotate has_tag conditions in qualified ranges with their tag display names.

    care_fe renders these through `ConditionOperationSummary`, which resolves the names by
    calling /api/v1/tag_config/ -- an authenticated endpoint. A patient reading this page
    has no CARE session, so the names are resolved here and attached as `tag_displays`.
    Every other condition operation is pure formatting and needs nothing from the server.

    Mutates in place. Unresolvable ids are dropped rather than shown raw: a bare UUID on a
    printed report is worse than an omission.
    """
    from care.emr.models.tag_config import TagConfig  # pyright: ignore[reportMissingImports]

    pending: list[dict[str, Any]] = []
    wanted: set[str] = set()

    for observation in observations:
        observation_definition = observation.get("observation_definition") or {}
        # A definition's components carry their own qualified_ranges -- that is where
        # care_fe looks for a component row's reference range.
        for definition in [observation_definition, *(observation_definition.get("component") or [])]:
            for qualified_range in definition.get("qualified_ranges") or []:
                for condition in qualified_range.get("conditions") or []:
                    if condition.get("operation") != "has_tag":
                        continue
                    value = condition.get("value")
                    raw = value.get("value") if isinstance(value, dict) else None
                    if not isinstance(raw, str) or not raw:
                        continue
                    ids = [part for part in raw.split(",") if part]
                    condition["tag_ids"] = ids
                    wanted.update(ids)
                    pending.append(condition)

    if not pending:
        return

    # Keyed by str: external_id is a UUIDField, so values_list yields UUID objects, while
    # the ids parsed out of a condition are strings.
    displays = {
        str(external_id): display
        for external_id, display in TagConfig.objects.filter(external_id__in=wanted).values_list(
            "external_id", "display"
        )
    }
    for condition in pending:
        condition["tag_displays"] = [
            displays[tag_id] for tag_id in condition.pop("tag_ids", []) if displays.get(tag_id)
        ]


def _build_diagnostic_report(link: DocumentLink) -> dict[str, Any]:
    """The same body care_fe's authenticated retrieve endpoint returns, plus attachments.

    Reusing core's read spec is the point: the page renders a copy of care_fe's print
    view, so it must receive the shape that view already consumes. A hand-rolled payload
    would be a second thing to keep in step with core.
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
    serialized = DiagnosticReportRetrieveSpec.serialize(report).to_json()
    _resolve_condition_tag_displays(serialized.get("observations") or [])
    return {
        "report": serialized,
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
