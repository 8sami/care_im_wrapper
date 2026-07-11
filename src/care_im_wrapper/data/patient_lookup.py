"""Staff-only patient search by phone number or name."""

import logging
from typing import Any

from care.security.authorization.base import AuthorizationController  # type: ignore[import-untyped]

from care_im_wrapper.core.sanitize import mask_phone_number, normalize_phone_number
from care_im_wrapper.data.exceptions import InvalidQueryError, NoDataError, PermissionDeniedError
from care_im_wrapper.models import ConversationSession
from care_im_wrapper.settings import plugin_settings

logger = logging.getLogger(__name__)


def search_patients(actor, query: str) -> list[dict[str, Any]]:
    """
    Returns list of {external_id, name, phone_number} dicts, max DATA_FETCH_LIMIT results,
    scoped to patients the actor can access via their facility/organization roles.
    Raises PermissionDeniedError if actor is not staff.
    Raises InvalidQueryError if the query is shorter than PATIENT_SEARCH_MIN_QUERY_LENGTH.
    Raises NoDataError if search returns nothing (including no accessible matches).
    """
    if actor.user_type != ConversationSession.UserType.STAFF.value:
        raise PermissionDeniedError("Patient lookup is staff only.")

    min_length = int(plugin_settings.PATIENT_SEARCH_MIN_QUERY_LENGTH)
    if len(query.strip()) < min_length:
        raise InvalidQueryError(f"Please enter at least {min_length} characters to search.")

    from care.emr.models.patient import Patient  # type: ignore[import-untyped]

    if normalize_phone_number(query).startswith("+") or query.isdigit():
        qs = Patient.objects.filter(phone_number__icontains=query)
    else:
        qs = Patient.objects.filter(name__icontains=query)

    # Scopes to patients visible to the actor via facility/org membership, the
    # same primitive care/emr/api/viewsets/patient.py uses for PatientViewSet.list.
    qs = AuthorizationController.call("get_filtered_patients", qs, actor.instance)

    results = []
    for p in qs.distinct()[: plugin_settings.DATA_FETCH_LIMIT]:
        results.append(
            {
                "external_id": str(p.external_id),
                "name": p.name,
                "phone_number": mask_phone_number(p.phone_number),
            }
        )

    if not results:
        raise NoDataError

    return results
