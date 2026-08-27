"""Structured return types for all data fetchers.

Every string here is already display-ready: dates humanized, choices turned into labels. The
renderers lay these out; they never reach back into the ORM to look something up.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DosageLine:
    """One entry of ``MedicationRequest.dosage_instruction``."""

    dosage: str  # "" when not recorded; the renderers apply the "-" fallback
    frequency: str
    additional_instructions: tuple[str, ...]
    duration: str
    sig: str  # route / method / site, as "Via Oral route by X to Y"
    is_non_unit_dose: bool  # a dose range, or a quantity other than 1


@dataclass(frozen=True)
class MedicationRecord:
    """One ``MedicationRequest`` within a prescription."""

    name: str  # care_fe's displayMedicationName
    status: str
    lines: tuple[DosageLine, ...]
    note: str | None = None


@dataclass(frozen=True)
class PrescriptionRecord:
    """One ``MedicationRequestPrescription`` and the medications on it."""

    name: str | None
    status: str
    prescribed_by: str | None
    prescribed_on: str
    facility: str | None  # prescription.encounter.facility.name
    note: str | None
    medications: tuple[MedicationRecord, ...]


@dataclass(frozen=True)
class PrescriptionChoiceRecord:
    """One row of the prescription picker -- care_fe's PrescriptionListSelector card."""

    prescribed_on: str
    prescribed_by: str | None
    name: str | None
    external_id: str = ""  # maps a chosen row back to its prescription


@dataclass(frozen=True)
class EncounterRecord:
    date: str
    facility: str
    status: str
    encounter_class: str
    external_id: str = ""  # maps a chosen row back to its encounter; never displayed

    @property
    def name(self) -> str:
        """The document pick-list keys rows by `name`; the facility identifies an encounter."""
        return self.facility


@dataclass(frozen=True)
class AppointmentRecord:
    subject: str
    facility: str  # SchedulableResource.facility.name -- unconditional, any resource_type
    status: str
    date: str
    time_slot: str  # e.g. "10:00 am - 10:30 am"


@dataclass(frozen=True)
class LabReportRecord:
    name: str
    date: str
    status: str
    external_id: str = ""  # maps a chosen row back to its report; never displayed


@dataclass(frozen=True)
class ProcedureRecord:
    name: str
    date: str
    status: str


@dataclass(frozen=True)
class PatientSummary:
    name: str | None
    date_of_birth: str | None  # a formatted dob, or "Year of birth: YYYY"
    blood_group: str | None
    gender: str | None
    phone: str | None
