"""Staff-only patient search by phone number or name."""

import logging
from typing import Any

from care.security.authorization.base import AuthorizationController  # type: ignore[import-untyped]

from care_im_wrapper.core.sanitize import mask_phone_number, normalize_phone_number
from care_im_wrapper.data.exceptions import NoDataError, PermissionDeniedError
from care_im_wrapper.models import ConversationSession
from care_im_wrapper.settings import plugin_settings

logger = logging.getLogger(__name__)


def search_patients(actor, query: str) -> list[dict[str, Any]]:
    """
    Returns list of {id, name, phone_number} dicts, max 10 results.
    Raises PermissionDeniedError if actor is not staff or lacks permission.
    Raises NoDataError if search returns nothing.
    """
    if actor.user_type != ConversationSession.UserType.STAFF.value:
        raise PermissionDeniedError("Patient lookup is staff only.")

    from care.emr.models.patient import Patient  # type: ignore[import-untyped]

    if not AuthorizationController.call("can_create_patient", actor.instance):
        raise PermissionDeniedError("Insufficient permissions for patient lookup.")

    if normalize_phone_number(query).startswith("+") or query.isdigit():
        qs = Patient.objects.filter(phone_number__icontains=query)
    else:
        qs = Patient.objects.filter(name__icontains=query)

    results = []
    for p in qs.distinct()[: plugin_settings.DATA_FETCH_LIMIT]:
        results.append(
            {
                "id": p.id,
                "external_id": str(p.external_id),
                "name": p.name,
                "phone_number": mask_phone_number(p.phone_number),
            }
        )

    if not results:
        raise NoDataError

    return results
