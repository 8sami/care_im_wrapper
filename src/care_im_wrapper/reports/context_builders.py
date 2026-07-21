"""Declarative descriptions of the objects a NotificationTrigger fires with.

Mirrors CARE core's report context builders (``care.emr.reports.context_builder
.data_points``) -- reuses core's ``Field`` / ``SingleObjectContextBuilder`` but
registers into this module's own ``NOTIFICATION_CONTEXT_REGISTRY``, not core's.
Each field's ``preview_value`` powers the preview endpoint without a DB row.
"""

from __future__ import annotations

from collections.abc import Iterator

from care.emr.reports.context_builder.data_points.base import (  # pyright: ignore[reportMissingImports]
    Field,
    SingleObjectContextBuilder,
)


class _NestedContext(SingleObjectContextBuilder):
    """A context reached by descending one attribute of its parent, e.g.
    ``object.token_slot.resource``. In preview mode the base class never calls
    ``get_context``; fields short-circuit to their ``preview_value`` instead."""

    def get_context(self):
        return getattr(self.parent_context, self.parent_attribute)


class PatientContext(_NestedContext):
    name = Field(
        display="Patient name",
        preview_value="Jane Doe",
        description="Full name of the patient the booking is for",
    )


class UserContext(_NestedContext):
    full_name = Field(
        display="Full name",
        preview_value="Dr. Ada Lovelace",
        description="Full name of the practitioner",
    )


class FacilityContext(_NestedContext):
    name = Field(
        display="Facility name",
        preview_value="City Care Hospital",
        description="Name of the facility",
    )


class ResourceContext(_NestedContext):
    user = Field(
        display="Practitioner",
        target_context=UserContext,
        description="The practitioner this schedulable resource represents",
    )
    facility = Field(
        display="Facility",
        target_context=FacilityContext,
        description="The facility this resource belongs to",
    )


class TokenSlotContext(_NestedContext):
    start_datetime = Field(
        display="Start time",
        field_type="datetime",
        preview_value="2026-07-20T10:30:00+05:30",
        description="Scheduled start of the slot (apply the |date / |time filters to format it)",
    )
    resource = Field(
        display="Resource",
        target_context=ResourceContext,
        description="The schedulable resource (practitioner + facility) for this slot",
    )


class TokenBookingContext(SingleObjectContextBuilder):
    """Context for the ``related_object`` of the appointment_confirmed/cancelled/
    rescheduled triggers. Object fields use ``object.<path>``; extra_context_fields
    are flat keys merged from ``variable_values`` / ``variable_overrides``."""

    __display_name__ = "Appointment booking"
    __description__ = "The appointment booking that triggered the notification"

    patient = Field(
        display="Patient",
        target_context=PatientContext,
        description="The patient the appointment is for",
    )
    token_slot = Field(
        display="Slot",
        target_context=TokenSlotContext,
        description="The scheduled slot for the appointment",
    )

    # Flat keys supplied at fire time via variable_values / default_variable_values,
    # not derivable from the object itself. Addressed without an `object.` prefix.
    extra_context_fields: dict[str, Field] = {
        "status": Field(
            display="Status",
            preview_value="confirmed",
            description="Human-readable booking status at the moment the event fired",
        ),
    }


class ServiceRequestContext(_NestedContext):
    title = Field(
        display="Service request name",
        preview_value="Complete Blood Count",
        description="Name of the service request the diagnostic report fulfills",
    )
    created_date = Field(
        display="Service request created at",
        field_type="datetime",
        preview_value="2026-07-19T10:30:00+05:30",
        description="When the service request was created (apply the |date / |time filters to format it)",
    )


class DiagnosticReportContext(SingleObjectContextBuilder):
    """Context for the ``related_object`` of the document_ready_update trigger."""

    __display_name__ = "Diagnostic report"
    __description__ = "The diagnostic report that triggered the notification"

    patient = Field(
        display="Patient",
        target_context=PatientContext,
        description="The patient the diagnostic report is for",
    )
    service_request = Field(
        display="Service request",
        target_context=ServiceRequestContext,
        description="The service request this diagnostic report fulfills",
    )

    # Supplied at fire time via variable_values, not derivable from the DiagnosticReport.
    extra_context_fields: dict[str, Field] = {
        "document_type": Field(
            display="Document type",
            preview_value="diagnostic_report",
            description="Label of the document being made available",
        ),
        "document_url_suffix": Field(
            display="Document URL suffix",
            preview_value="preview-token",
            description="Dynamic suffix filled into the WhatsApp template's URL button",
        ),
    }


class NotificationContextRegistry:
    """Maps a trigger ``context_slug`` to its context class. Populated by each
    handler module (mirrors ``models.notification._FACILITY_RESOLVERS``)."""

    def __init__(self) -> None:
        self._contexts: dict[str, type[SingleObjectContextBuilder]] = {}

    def register(self, slug: str, context_class: type[SingleObjectContextBuilder]) -> None:
        self._contexts[slug] = context_class

    def get(self, slug: str) -> type[SingleObjectContextBuilder] | None:
        return self._contexts.get(slug)

    def slugs(self) -> Iterator[str]:
        return iter(self._contexts)

    def __contains__(self, slug: object) -> bool:
        return slug in self._contexts


NOTIFICATION_CONTEXT_REGISTRY = NotificationContextRegistry()
