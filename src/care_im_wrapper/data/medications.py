"""Fetch medication requests for the authenticated actor."""

from typing_extensions import Any

from care_im_wrapper.auth.actor import Actor
from care_im_wrapper.data.base import cached_fetch, humanize_choice
from care_im_wrapper.data.common import resolve_target_patient
from care_im_wrapper.data.exceptions import NoDataError
from care_im_wrapper.models import ConversationSession
from care_im_wrapper.settings import plugin_settings


@cached_fetch(timeout_seconds=int(plugin_settings.DATA_CACHE_TIMEOUT_SECONDS))
def fetch_medications(actor: Actor, session: ConversationSession) -> list[dict[str, Any]]:
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

        # We return structured data. The Handler will decide how to display this.
        items.append(
            {
                "name": medication_name,
                "status": status,
                "created_date": med.created_date.isoformat(),
            }
        )

    return items


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
