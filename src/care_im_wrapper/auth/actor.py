"""Resolve the real CARE model instance behind an authenticated session."""

import logging
from dataclasses import dataclass
from typing import Any

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

    try:
        if session.user_type == "patient":
            return Actor(
                user_type="patient",
                instance=Patient.objects.get(id=session.user_id),
            )
        if session.user_type == "staff":
            return Actor(
                user_type="staff",
                instance=User.objects.get(id=session.user_id),
            )
        logger.error("resolve_actor: unknown user_type %s", session.user_type)
        return None
    except Exception as exc:
        logger.warning(
            "resolve_actor: %s id=%s not found: %s",
            session.user_type,
            session.user_id,
            exc,
        )
        return None
