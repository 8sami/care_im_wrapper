"""Structured return types for all data fetchers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DosageLine:
    """One entry of ``MedicationRequest.dosage_instruction``."""

    dosage: str  # "" when not recorded; renderers apply the "-" fallback
    frequency: str
    additional_instructions: tuple[str, ...]
    duration: str
    sig: str  # route / method / site, as "Via Oral route by X to Y"
    is_non_unit_dose: bool  # dose ranges, or quantity != 1


@dataclass(frozen=True)
class MedicationRecord:
    """One ``MedicationRequest`` within a prescription."""

    name: str  # displayMedicationName
    status: str  # already humanized via humanize_choice()
    is_inactive: bool  # care_fe dims these
    lines: tuple[DosageLine, ...]
    note: str | None = None


@dataclass(frozen=True)
class PrescriptionRecord:
    """One ``MedicationRequestPrescription`` and the medications on it."""

    name: str | None  # prescription.name
    status: str  # already humanized
    prescribed_by: str | None  # formatName(prescription.prescribed_by)
    prescribed_on: str  # humanized prescription.created_date
    facility: str | None  # prescription.encounter.facility.name
    note: str | None
    medications: tuple[MedicationRecord, ...]


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
        """The document pick-list keys rows by `name`; the facility identifies an encounter."""
        return self.facility


@dataclass(frozen=True)
class AppointmentRecord:
    subject: str
    facility: str  # SchedulableResource.facility.name -- unconditional, any resource_type
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
