"""Fetch medication requests for the authenticated actor."""

from care_im_wrapper.auth.actor import Actor
from care_im_wrapper.data.base import humanize_choice, numbered_list
from care_im_wrapper.data.common import resolve_target_patient
from care_im_wrapper.data.exceptions import NoDataError
from care_im_wrapper.models import ConversationSession


def fetch_medications(actor: Actor, session: ConversationSession) -> str:
    """
    patient: returns their own last 10 medications.
    staff:   returns medications for session.active_patient_external_id.
    Raises PermissionDeniedError, NoDataError, MissingContextError.
    """
    from care.emr.models.medication_request import MedicationRequest  # type: ignore[import-untyped]

    patient = resolve_target_patient(actor, session)
    queryset = MedicationRequest.objects.filter(patient=patient)
    records = queryset.order_by("-created_date")[:10]
    if not records:
        raise NoDataError

    items = []
    for med in records:
        status = humanize_choice(getattr(med, "status", None))

        medication_name = _extract_medication_name(med)

        dosage_parts = []
        dosage_instructions = getattr(med, "dosage_instruction", [])
        if isinstance(dosage_instructions, list):
            for instr in dosage_instructions:
                if isinstance(instr, dict):
                    display = instr.get("display") or instr.get("text")  # find the exact attribute
                    if display:
                        dosage_parts.append(str(display))
                    else:
                        parts = [v for k, v in instr.items() if v and k not in ("code", "system")]
                        if parts:
                            dosage_parts.append("; ".join(map(str, parts)))

                    timing = instr.get("timing", {})
                    if isinstance(timing, dict):
                        repeat = timing.get("repeat", {})
                        if isinstance(repeat, dict):
                            bounds = repeat.get("bounds_duration")
                            if isinstance(bounds, dict) and "value" in bounds and "unit" in bounds:
                                dosage_parts.append(f"(Duration: {bounds['value']} {bounds['unit']})")

                elif instr:
                    dosage_parts.append(str(instr))

        dosage_text = " | ".join(dosage_parts) if dosage_parts else ""

        item_details = [medication_name, f"Status: {status}"]
        if dosage_text:
            item_details.append(f"Dosage: {dosage_text}")

        note = getattr(med, "note", None)
        if note:
            item_details.append(f"Note: {note}")

        items.append(" | ".join(item_details))

    return numbered_list("Your recent medications:", items)


def _extract_medication_name(med) -> str:
    """
    Extracts a human-readable medication name from MedicationRequest.
    Uses the 'requested_product' relationship to fetch the product name
    from ProductKnowledge if available.
    """
    medication = getattr(med, "medication", None)
    if isinstance(medication, dict):
        name = medication.get("display") or medication.get("text")  # find the exact attribute
        if name:
            return name

    requested_product = getattr(med, "requested_product", None)
    if requested_product and hasattr(requested_product, "name"):
        return str(requested_product.name)

    if isinstance(medication, dict):
        return f"Medication ({str(medication)})"

    return str(medication) if medication else "Unknown medication"
