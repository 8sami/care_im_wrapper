"""
Structured return types for all data fetchers.
These are provider-agnostic data containers. No formatting happens here.
Rendering into OutboundMessage happens in conversation/renderers.py.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MedicationRecord:
    name: str
    status: str  # already humanized via humanize_choice()
    dosage: str | None = None  # human-readable dosage string, or None
    note: str | None = None


@dataclass(frozen=True)
class EncounterRecord:
    date: str  # already humanized via humanize_date()
    facility: str
    status: str  # already humanized via humanize_choice()
    encounter_class: str
    # Encounter.external_id, to map a rendered row back to its record. Not displayed.
    external_id: str = ""

    @property
    def name(self) -> str:
        """The document pick-list keys rows by `name`; the facility identifies an encounter
        to the patient (see conversation.handlers._enter_document_selection)."""
        return self.facility


@dataclass(frozen=True)
class AppointmentRecord:
    practitioner: str
    location: str
    status: str  # already humanized via humanize_choice()
    date: str  # already humanized via humanize_date()
    time_slot: str  # e.g. "10:00 am - 10:30 am"


@dataclass(frozen=True)
class LabReportRecord:
    name: str
    date: str  # already humanized via humanize_date()
    status: str  # already humanized via humanize_choice()
    # DiagnosticReport.external_id, to map a rendered row back to its record. Not displayed.
    external_id: str = ""


@dataclass(frozen=True)
class ProcedureRecord:
    name: str
    date: str  # already humanized via humanize_date()
    status: str  # already humanized via humanize_choice()


@dataclass(frozen=True)
class PatientSummary:
    name: str | None
    date_of_birth: str | None  # formatted dob or "Year of birth: YYYY"
    blood_group: str | None  # already humanized
    gender: str | None  # already humanized
    phone: str | None
