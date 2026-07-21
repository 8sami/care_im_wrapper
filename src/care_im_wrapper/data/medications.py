"""Fetch medication requests for the authenticated actor."""

from care_im_wrapper.auth.actor import Actor
from care_im_wrapper.data.base import ENTERED_IN_ERROR_STATUS, cached_fetch, humanize_choice
from care_im_wrapper.data.common import resolve_target_patient
from care_im_wrapper.data.exceptions import NoDataError
from care_im_wrapper.data.records import MedicationRecord
from care_im_wrapper.models import ConversationSession
from care_im_wrapper.settings import plugin_settings


@cached_fetch(timeout_seconds=int(plugin_settings.DATA_CACHE_TIMEOUT_SECONDS))
def fetch_medications(actor: Actor, session: ConversationSession) -> list[MedicationRecord]:
    """
    patient: returns their own last 10 medications.
    staff:   returns medications for session.active_patient_external_id.
    Raises PermissionDeniedError, NoDataError, MissingContextError.
    """
    from care.emr.models.medication_request import MedicationRequest  # type: ignore[import-untyped]

    patient = resolve_target_patient(actor, session)
    queryset = (
        MedicationRequest.objects.filter(patient=patient)
        .exclude(status=ENTERED_IN_ERROR_STATUS)
        .select_related("requested_product")
    )
    records = queryset.order_by("-created_date")[: plugin_settings.DATA_FETCH_LIMIT]
    if not records:
        raise NoDataError

    medication_records = []
    for med in records:
        status = humanize_choice(getattr(med, "status", None))
        name = _extract_medication_name(med)

        dosage = None
        if hasattr(med, "dosage_instruction") and med.dosage_instruction:
            instructions = med.dosage_instruction
            if isinstance(instructions, list):
                parts = []
                for inst in instructions:
                    if isinstance(inst, dict):
                        display_val = inst.get("display") or inst.get("text")
                        if display_val:
                            parts.append(display_val)
                        else:
                            # No free-text instruction; fall back to timing/duration.
                            timing = inst.get("timing", {})
                            if isinstance(timing, dict):
                                repeat = timing.get("repeat")
                                if isinstance(repeat, dict):
                                    duration = repeat.get("bounds_duration")
                                    if duration:
                                        parts.append(f"(Duration: {duration})")
                            elif inst:
                                parts.append(str(inst))
                    else:
                        parts.append(str(inst))
                dosage = " | ".join(parts) if parts else None
            elif isinstance(instructions, str):
                dosage = instructions

        note = getattr(med, "note", None)
        medication_records.append(MedicationRecord(name=name, status=status, dosage=dosage, note=note))

    return medication_records


def _extract_medication_name(med) -> str:
    """
    Extracts a human-readable medication name from MedicationRequest.
    Uses the 'requested_product' relationship to fetch the product name
    from ProductKnowledge if available.
    """
    medication = getattr(med, "medication", None)
    if isinstance(medication, dict):
        name = medication.get("display") or medication.get("text")
        if name:
            return name

    requested_product = getattr(med, "requested_product", None)
    if requested_product and hasattr(requested_product, "name"):
        return str(requested_product.name)

    if isinstance(medication, dict):
        return f"Medication ({str(medication)})"

    return str(medication) if medication else "Unknown medication"
