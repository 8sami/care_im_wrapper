from __future__ import annotations

import logging
from typing import Any

from care.emr.models.diagnostic_report import DiagnosticReport  # pyright: ignore[reportMissingImports]
from care.emr.models.service_request import ServiceRequest  # pyright: ignore[reportMissingImports]
from care.emr.resources.diagnostic_report.spec import (  # pyright: ignore[reportMissingImports]
    DiagnosticReportStatusChoices,
)
from care.emr.resources.service_request.spec import ServiceRequestStatusChoices  # pyright: ignore[reportMissingImports]
from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from care_im_wrapper.handlers.dispatch import track_previous_status
from care_im_wrapper.models.notification import _FACILITY_RESOLVERS
from care_im_wrapper.reports.context_builders import NOTIFICATION_CONTEXT_REGISTRY, DiagnosticReportContext
from care_im_wrapper.tasks import notify_document_ready

logger = logging.getLogger(__name__)

# Set on the document_ready_update trigger's context_slug.
DOCUMENT_READY_CONTEXT_SLUG = "diagnostic_report"


def _resolve_diagnostic_report_facility(report: DiagnosticReport) -> Any | None:
    return report.encounter.facility


_FACILITY_RESOLVERS[DiagnosticReport] = _resolve_diagnostic_report_facility
NOTIFICATION_CONTEXT_REGISTRY.register(DOCUMENT_READY_CONTEXT_SLUG, DiagnosticReportContext)


pre_save.connect(track_previous_status, sender=ServiceRequest)


@receiver(post_save, sender=ServiceRequest)
def on_service_request_post_save(
    sender: type[ServiceRequest], instance: ServiceRequest, created: bool, **kwargs: Any
) -> None:
    """Delivers on ServiceRequest completion, not on the DiagnosticReport reaching 'final':
    a final report can still be voided before the order completes, so 'completed' is the
    stable point to release it."""
    if created:
        return  # a service request is never created already 'completed'

    previous_status = getattr(instance, "_previous_status", None)
    if previous_status == instance.status or instance.status != ServiceRequestStatusChoices.completed:
        return

    # Latest still-final report; a voided or absent one matches nothing and sends nothing.
    report = (
        DiagnosticReport.objects.filter(
            service_request=instance,
            status=DiagnosticReportStatusChoices.final.value,
        )
        .order_by("-created_date")
        .first()
    )
    if report is None:
        return

    # Runs in a worker (can render a PDF) via on_commit, not a bare .delay(): under
    # ATOMIC_REQUESTS the worker could otherwise run before this write commits.
    report_external_id = str(report.external_id)
    transaction.on_commit(
        lambda: notify_document_ready.delay(report_external_id)  # pyright: ignore[reportFunctionMemberAccess]
    )
