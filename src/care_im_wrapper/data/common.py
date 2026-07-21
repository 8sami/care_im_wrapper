"""Shared resolution and authorization logic for all data fetchers."""

import logging

from care.security.authorization.base import AuthorizationController  # type: ignore[import-untyped]

from care_im_wrapper.data.exceptions import MissingContextError, PermissionDeniedError
from care_im_wrapper.models import ConversationSession

logger = logging.getLogger(__name__)


def resolve_target_patient(actor, session):
    """
    Resolves which Patient instance a fetcher should query against,
    and enforces RBAC for the staff case in one place.

    patient actor: returns actor.instance directly, no extra check needed
                   (a patient is always authorized to view their own record).
    staff actor:   resolves session.active_patient_external_id to a Patient,
                   then enforces can_view_patient_obj before returning it.

    Raises MissingContextError if staff has no patient selected.
    Raises PermissionDeniedError if staff lacks permission on the resolved patient.
    """
    if actor.user_type == ConversationSession.UserType.PATIENT.value:
        return actor.instance

    from care.emr.models.patient import Patient  # type: ignore[import-untyped]

    if not session.active_patient_external_id:
        raise MissingContextError("No patient selected. Use Patient lookup first.")

    try:
        patient = Patient.objects.get(external_id=session.active_patient_external_id)
    except Patient.DoesNotExist:
        raise MissingContextError("Selected patient not found.") from None

    authorize_patient_access(actor, patient)

    return patient


def authorize_patient_access(actor, patient) -> None:
    """The identity/RBAC scope resolve_target_patient enforces, for callers that already
    have a resolved Patient rather than a session.

    A patient actor is only authorized for their own record; a staff actor goes through
    can_view_patient_obj. Raises PermissionDeniedError on either failure.
    """
    if actor.user_type == ConversationSession.UserType.PATIENT.value:
        if patient.id != actor.instance.id:
            raise PermissionDeniedError
        return

    if not AuthorizationController.call("can_view_patient_obj", actor.instance, patient):
        raise PermissionDeniedError
