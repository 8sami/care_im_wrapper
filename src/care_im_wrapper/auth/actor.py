"""Resolve the real CARE model instance behind an authenticated session."""

import logging
from dataclasses import dataclass
from typing import Any

from care_im_wrapper.models import ConversationSession

logger = logging.getLogger(__name__)


@dataclass
class Actor:
    """
    Wraps the real CARE entity behind an authenticated session.
    Pass `instance` to AuthorizationController.call() — never a service account.
    """

    user_type: str  # "patient" | "staff"
    instance: Any  # Patient | User — resolved from ConversationSession


def resolve_actor(session) -> "Actor | None":
    """
    Returns None if the CARE record no longer exists (deleted/merged after auth).
    Caller must call session.logout() and prompt re-authentication on None.
    """
    from care.emr.models.patient import Patient  # pyright: ignore[reportMissingImports]
    from care.users.models import User  # pyright: ignore[reportMissingImports]

    if session.user_type == ConversationSession.UserType.PATIENT:
        return Actor(
            user_type=ConversationSession.UserType.PATIENT,
            instance=Patient.objects.get(id=session.user_id),
        )
    if session.user_type == ConversationSession.UserType.STAFF:
        return Actor(
            user_type=ConversationSession.UserType.STAFF,
            instance=User.objects.get(id=session.user_id),
        )

    logger.warning(
        "resolve_actor: %s id=%s not found",
        session.user_type,
        session.user_id,
    )
    return None
