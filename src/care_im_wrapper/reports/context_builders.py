"""Declarative descriptions of the objects a NotificationTrigger fires with."""

from __future__ import annotations

from collections.abc import Iterator

from care.emr.reports.context_builder.data_points.base import (  # pyright: ignore[reportMissingImports]
    Field,
    SingleObjectContextBuilder,
)


class _NestedContext(SingleObjectContextBuilder):
    """A context reached by descending one attribute of its parent, e.g."""

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
    """Context for the ``related_object`` of the appointment_confirmed/cancelled/."""

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

    extra_context_fields: dict[str, Field] = {
        "status": Field(
            display="Status",
            preview_value="confirmed",
            description="Human-readable booking status at the moment the event fired",
        ),
        "doctor_name": Field(
            display="Practitioner / resource",
            preview_value="Ada Lovelace",
            description=(
                "Who or what the appointment is with: the practitioner's name, or "
                '"Cardiology Location" / "OP HealthcareService" for resource types that '
                "have no practitioner"
            ),
        ),
    }


class AppointmentReminderContext(TokenBookingContext):
    """Same TokenBooking object as TokenBookingContext, but the reminder template names."""

    __display_name__ = "Appointment reminder"
    __description__ = "The upcoming appointment booking the reminder is for"

    extra_context_fields: dict[str, Field] = {
        **{key: field for key, field in TokenBookingContext.extra_context_fields.items() if key != "status"},
        "event": Field(
            display="Event",
            preview_value="appointment",
            description="What is coming up, lowercase, for use mid-sentence",
        ),
        "event_header": Field(
            display="Event (header)",
            preview_value="appointment",
            description="The same event label, for the message header",
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


class PatientNotificationContext(SingleObjectContextBuilder):
    """Context for the ``related_object`` of the patient_registered / patient_discharged."""

    __display_name__ = "Patient"
    __description__ = "The patient the lifecycle notification is about"

    name = Field(
        display="Patient name",
        preview_value="Jane Doe",
        description="Full name of the patient",
    )

    extra_context_fields: dict[str, Field] = {
        "patient_id": Field(
            display="Patient identifier",
            preview_value="#12909",
            description="Display identifier for the patient (configured instance identifier, else external id)",
        ),
        "action": Field(
            display="Action",
            preview_value="discharged",
            description="What happened, lowercase, for use mid-sentence",
        ),
        "header_action": Field(
            display="Action (header)",
            preview_value="Discharged",
            description="The same action, capitalised, for the message header",
        ),
        "date_and_time": Field(
            display="Date and time",
            preview_value="12 May 2026, 04:30 pm",
            description="When the event happened",
        ),
    }


class AccountContext(_NestedContext):
    name = Field(
        display="Account name",
        preview_value="Jane Doe",
        description="Name on the billing account the invoice belongs to",
    )


class InvoiceContext(SingleObjectContextBuilder):
    """Context for the ``related_object`` of the invoice_issued / payment_recorded."""

    __display_name__ = "Invoice"
    __description__ = "The invoice the billing notification is about"

    number = Field(
        display="Invoice number (raw)",
        preview_value="#1322",
        description=(
            "The invoice number exactly as stored -- blank when the facility's identifier "
            "expression produced none. Prefer the handler-supplied 'Invoice number' below, "
            "which never resolves to an empty value."
        ),
    )
    patient = Field(
        display="Patient",
        target_context=PatientContext,
        description="The patient the invoice is for",
    )
    account = Field(
        display="Account",
        target_context=AccountContext,
        description="The billing account the invoice belongs to",
    )

    extra_context_fields: dict[str, Field] = {
        "amount": Field(
            display="Amount",
            preview_value="14,000.00",
            description="Invoice total for an issue, or the amount tendered for a payment",
        ),
        "invoice_number": Field(
            display="Invoice number",
            preview_value="#1322",
            description=(
                "The invoice number to quote, falling back to the invoice's external id "
                "when no number was assigned. Never blank, so it is safe to place in a "
                "template parameter."
            ),
        ),
        "status": Field(
            display="Status",
            preview_value="confirmed",
            description="What happened, lowercase, for use mid-sentence",
        ),
        "header_status": Field(
            display="Status (header)",
            preview_value="Confirmed",
            description="The same status, capitalised, for the message header",
        ),
    }


class TokenQueueContext(_NestedContext):
    date = Field(
        display="Queue date",
        field_type="date",
        preview_value="2026-07-20",
        description="The day this queue is for (apply the |date filter to format it)",
    )


class TokenContext(SingleObjectContextBuilder):
    """Context for the ``related_object`` of the wait_time_update trigger."""

    __display_name__ = "Token"
    __description__ = "The queue token the waiting-time notification is about"

    patient = Field(
        display="Patient",
        target_context=PatientContext,
        description="The patient the token was issued to",
    )
    queue = Field(
        display="Queue",
        target_context=TokenQueueContext,
        description="The queue the token belongs to",
    )

    extra_context_fields: dict[str, Field] = {
        "event": Field(
            display="Event",
            preview_value="token #12321",
            description="What the patient is waiting on, e.g. the token number",
        ),
        "service_name": Field(
            display="Service",
            preview_value="appointment with Dr. Ada Lovelace",
            description="What the token is for -- the practitioner, service or location behind the queue",
        ),
        "waiting_time": Field(
            display="Estimated waiting time",
            preview_value="45 minutes",
            description=(
                "Estimated wait: time until the booked slot starts for a scheduled token, "
                "or how many tokens are still ahead in the queue for a walk-in"
            ),
        ),
    }


class NotificationContextRegistry:
    """Maps a trigger ``context_slug`` to its context class. Populated by each."""

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
