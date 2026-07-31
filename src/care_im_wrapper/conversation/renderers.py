"""Renders structured data records (from data/) into OutboundMessage objects."""

from __future__ import annotations

from care_im_wrapper.conversation.messages import OutboundMessage
from care_im_wrapper.conversation.templates import _msg
from care_im_wrapper.data.records import (
    AppointmentRecord,
    DosageLine,
    EncounterRecord,
    LabReportRecord,
    MedicationRecord,
    PatientSummary,
    PrescriptionRecord,
    ProcedureRecord,
)
from care_im_wrapper.settings import plugin_settings


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    # max(0, ...): a negative slice would return the message's tail, not its head.
    keep = max(0, max_chars - int(plugin_settings.WHATSAPP_TRUNCATE_RESERVE_CHARS))
    return text[:keep] + "\n... (truncated)"


#: One nesting level, so blocks compose without rewriting inner depths.
INDENT = "   "


def indent(text: str, levels: int = 1) -> str:
    """Indents every line of a block, not just the first."""
    pad = INDENT * levels
    return "\n".join(pad + line if line else line for line in text.split("\n"))


def titled(title: str, *details: str | None) -> str:
    """A title, then its details one level in. A detail may itself be a `titled()` block,."""
    return "\n".join([title, *(indent(detail) for detail in details if detail)])


def numbered_block(header: str, lines: list[str], max_chars: int, start: int = 1) -> str:
    """A header, a blank line, then `lines` numbered from `start`, truncated to `max_chars`."""
    parts = [header, ""]
    for number, entry in enumerate(lines, start=start):
        marker = f"{number}.  "
        first, *rest = entry.split("\n")
        hanging = " " * len(marker)
        parts.append(marker + first)
        parts.extend(hanging + line if line else line for line in rest)
    return _truncate("\n".join(parts), max_chars)


def render_patient_search_results(prompt: str, results: list[str], max_chars: int, start: int = 1) -> OutboundMessage:
    """Assembles a numbered list for patient search fallbacks with truncation."""
    return OutboundMessage(text=numbered_block(prompt, results, max_chars, start))


#: "Duration: -" says not recorded, where a missing line reads as if there were none.
_NOT_RECORDED = "-"


def _render_dosage_line(line: DosageLine) -> str:
    """One dosage_instruction, as care_fe's four columns turned into labelled lines."""
    frequency = ", ".join(part for part in (line.frequency or _NOT_RECORDED, *line.additional_instructions) if part)
    out = [
        _msg("medication_dosage", dosage=line.dosage or _NOT_RECORDED),
        _msg("medication_frequency", frequency=frequency),
    ]
    out.append(_msg("medication_duration", duration=line.duration or _NOT_RECORDED))
    out.append(_msg("medication_instructions", instructions=line.sig or _NOT_RECORDED))
    return "\n".join(out)


def _render_medication(record: MedicationRecord) -> str:
    """A medication and its dosage lines -- a `titled` block, like every other record."""
    # A tapered course gets numbered steps; a single instruction needs none.
    numbered = len(record.lines) > 1
    dosages = [
        titled(_msg("medication_step", step=index), _render_dosage_line(line))
        if numbered
        else _render_dosage_line(line)
        for index, line in enumerate(record.lines, start=1)
    ]

    return titled(
        _msg("medication_line", name=record.name, status=record.status),
        *dosages,
        None if record.lines else _msg("medication_no_dosage"),
        _msg("medication_note", note=record.note) if record.note else None,
    )


def render_prescriptions(records: list[PrescriptionRecord], max_chars: int, start: int = 1) -> OutboundMessage:
    """Prescriptions, each with the medications on it -- care's two-level read shape."""
    blocks = []
    for record in records:
        if record.status:
            title = _msg("prescription_line", name=record.name or _msg("prescription_untitled"), status=record.status)
            date_line = _msg("prescription_date", date=record.prescribed_on)
        else:
            title = _msg("medications_on_date", date=record.prescribed_on)
            date_line = None

        medications = (
            [_render_medication(medication) for medication in record.medications]
            if record.medications
            else [_msg("prescription_no_medications")]
        )

        # A medication is just another detail; titled() indents every line it is given.
        blocks.append(
            titled(
                title,
                date_line,
                _msg("prescription_prescribed_by", prescribed_by=record.prescribed_by or _NOT_RECORDED),
                _msg("prescription_facility", facility=record.facility) if record.facility else None,
                *medications,
                _msg("prescription_note", note=record.note) if record.note else None,
            )
        )
    return OutboundMessage(text=numbered_block(_msg("prescriptions_header"), blocks, max_chars, start))


def render_encounters(records: list[EncounterRecord], max_chars: int, start: int = 1) -> OutboundMessage:
    lines = [
        titled(
            _msg("encounter_line", facility=r.facility, status=r.status),
            _msg("encounter_date", date=r.date),
            _msg("encounter_type", encounter_class=r.encounter_class),
        )
        for r in records
    ]
    return OutboundMessage(text=numbered_block(_msg("encounters_header"), lines, max_chars, start))


def render_appointments(records: list[AppointmentRecord], max_chars: int, start: int = 1) -> OutboundMessage:
    lines = [
        titled(
            _msg("appointment_line", subject=r.subject, status=r.status),
            _msg("appointment_facility", facility=r.facility),
            _msg("appointment_date", date=r.date),
            _msg("appointment_time", time_slot=r.time_slot),
        )
        for r in records
    ]
    return OutboundMessage(text=numbered_block(_msg("appointments_header"), lines, max_chars, start))


def render_lab_reports(records: list[LabReportRecord], max_chars: int, start: int = 1) -> OutboundMessage:
    lines = [
        titled(
            _msg("lab_report_line", name=r.name, status=r.status),
            _msg("lab_report_date", date=r.date),
        )
        for r in records
    ]
    return OutboundMessage(text=numbered_block(_msg("lab_reports_header"), lines, max_chars, start))


def render_procedures(records: list[ProcedureRecord], max_chars: int, start: int = 1) -> OutboundMessage:
    lines = [
        titled(
            _msg("procedure_line", name=r.name, status=r.status),
            _msg("procedure_date", date=r.date),
        )
        for r in records
    ]
    return OutboundMessage(text=numbered_block(_msg("procedures_header"), lines, max_chars, start))


def render_summary(summary: PatientSummary, max_chars: int) -> OutboundMessage:
    """The patient summary: one record, not a list, so it takes no numbering start."""
    not_recorded = _msg("summary_not_recorded")
    lines = [
        _msg("summary_name", value=summary.name or not_recorded),
        _msg("summary_dob", value=summary.date_of_birth or not_recorded),
        _msg("summary_blood_group", value=summary.blood_group or not_recorded),
        _msg("summary_gender", value=summary.gender or not_recorded),
        _msg("summary_phone", value=summary.phone or not_recorded),
    ]
    return OutboundMessage(text=_truncate(_msg("summary_header") + "\n\n" + "\n".join(lines), max_chars))
