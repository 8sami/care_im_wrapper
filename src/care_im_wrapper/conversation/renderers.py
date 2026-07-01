"""
Renders structured data records (from data/) into OutboundMessage objects.

This is the only place where record fields are formatted into human-readable
strings for delivery. Template keys from templates.py are applied here.

Adding a new channel: no changes needed here. The OutboundMessage abstraction
handles provider-specific rendering downstream.
"""

from __future__ import annotations

from care_im_wrapper.conversation.messages import OutboundMessage
from care_im_wrapper.conversation.templates import _msg
from care_im_wrapper.data.records import (
    AppointmentRecord,
    EncounterRecord,
    LabReportRecord,
    MedicationRecord,
    PatientSummary,
    ProcedureRecord,
)
from care_im_wrapper.settings import plugin_settings


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - plugin_settings.WHATSAPP_TITLE_TRUNCATE] + "\n... (truncated)"


def _numbered_block(header: str, lines: list[str], max_chars: int) -> str:
    """Assembles a numbered list into a single plain-text string."""
    parts = [header, ""]
    for i, line in enumerate(lines, start=1):
        parts.append(f"{i}. {line}")
    return _truncate("\n".join(parts), max_chars)


def render_patient_search_results(prompt: str, results: list[str], max_chars: int) -> OutboundMessage:
    """Assembles a numbered list for patient search fallbacks with truncation."""
    parts = [prompt, ""]
    for i, line in enumerate(results, start=1):
        parts.append(f"{i}. {line}")
    return OutboundMessage(text=_truncate("\n".join(parts), max_chars))


def render_medications(records: list[MedicationRecord], max_chars: int) -> OutboundMessage:
    header = _msg("medications_header")
    lines = []
    for r in records:
        line = _msg("medication_line", name=r.name, status=r.status)
        if r.dosage:
            line += "\n   " + _msg("medication_dosage", dosage=r.dosage)
        if r.note:
            line += f"\n   Note: {r.note}"
        lines.append(line)
    return OutboundMessage(text=_numbered_block(header, lines, max_chars))


def render_encounters(records: list[EncounterRecord], max_chars: int) -> OutboundMessage:
    header = _msg("encounters_header")
    lines = [_msg("encounter_line", date=r.date, facility=r.facility, status=r.status) for r in records]
    return OutboundMessage(text=_numbered_block(header, lines, max_chars))


def render_appointments(records: list[AppointmentRecord], max_chars: int) -> OutboundMessage:
    header = _msg("appointments_header")
    lines = []
    for r in records:
        main = _msg("appointment_line", practitioner=r.practitioner, location=r.location)
        detail = _msg("appointment_detail", status=r.status, date=r.date, time_slot=r.time_slot)
        lines.append(f"{main}\n   {detail}")
    return OutboundMessage(text=_numbered_block(header, lines, max_chars))


def render_lab_reports(records: list[LabReportRecord], max_chars: int) -> OutboundMessage:
    header = _msg("lab_reports_header")
    lines = [_msg("lab_report_line", name=r.name, date=r.date, status=r.status) for r in records]
    return OutboundMessage(text=_numbered_block(header, lines, max_chars))


def render_procedures(records: list[ProcedureRecord], max_chars: int) -> OutboundMessage:
    header = _msg("procedures_header")
    lines = [_msg("procedure_line", name=r.name, date=r.date, status=r.status) for r in records]
    return OutboundMessage(text=_numbered_block(header, lines, max_chars))


def render_summary(summary: PatientSummary, max_chars: int) -> OutboundMessage:
    not_recorded = _msg("summary_not_recorded")
    lines = [
        _msg("summary_name", value=summary.name or not_recorded),
        _msg("summary_dob", value=summary.date_of_birth or not_recorded),
        _msg("summary_blood_group", value=summary.blood_group or not_recorded),
        _msg("summary_gender", value=summary.gender or not_recorded),
        _msg("summary_phone", value=summary.phone or not_recorded),
    ]
    return OutboundMessage(text=_truncate(_msg("summary_header") + "\n\n" + "\n".join(lines), max_chars))
