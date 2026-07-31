"""Staff-only patient search by phone number or name."""

import logging
from types import SimpleNamespace
from typing import Any

from care.security.authorization.base import AuthorizationController  # type: ignore[import-untyped]

from care_im_wrapper.core.sanitize import mask_phone_number, normalize_phone_number
from care_im_wrapper.data.exceptions import InvalidQueryError, PermissionDeniedError
from care_im_wrapper.data.pagination import Page, map_page, paginate_or_raise
from care_im_wrapper.models import ConversationSession
from care_im_wrapper.settings import plugin_settings

logger = logging.getLogger(__name__)

_FIRST_PAGE = SimpleNamespace(data_page=0)


def search_patients(actor, query: str, session: Any = None) -> Page:
    """One page of {external_id, name, phone_number} dicts the actor can access."""
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

    qs = AuthorizationController.call("get_filtered_patients", qs, actor.instance)

    page = paginate_or_raise(qs.distinct().order_by("name", "id"), session or _FIRST_PAGE)

    def build(p) -> dict[str, Any]:
        return {
            "external_id": str(p.external_id),
            "name": p.name,
            "phone_number": mask_phone_number(p.phone_number),
        }

    return map_page(page, build)
