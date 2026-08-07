"""Patient lifecycle notifications: registration and discharge."""

from __future__ import annotations

import logging
from typing import Any

from care.emr.models.encounter import Encounter  # pyright: ignore[reportMissingImports]
from care.emr.models.patient import Patient  # pyright: ignore[reportMissingImports]
from care.emr.resources.encounter.constants import StatusChoices  # pyright: ignore[reportMissingImports]
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from care_im_wrapper.data.base import humanize_date, humanize_time
from care_im_wrapper.handlers.dispatch import (
    NotificationRecipientSpec,
    fire_notification_event,
    track_previous_field,
)
from care_im_wrapper.models.notification import _FACILITY_RESOLVERS
from care_im_wrapper.reports.context_builders import NOTIFICATION_CONTEXT_REGISTRY, PatientNotificationContext
from care_im_wrapper.settings import plugin_settings

logger = logging.getLogger(__name__)

# Set on the patient_registered / patient_discharged triggers' context_slug.
PATIENT_CONTEXT_SLUG = "patient"


def _registering_user_facility(patient: Patient) -> Any | None:
    """The facility of whoever created the patient record."""
    created_by_id = getattr(patient, "created_by_id", None)
    if not created_by_id:
        return None

    from care.emr.models.organization import FacilityOrganizationUser  # pyright: ignore[reportMissingImports]

    membership = (
        FacilityOrganizationUser.objects.filter(user_id=created_by_id)
        .select_related("organization__facility")
        .order_by("id")
        .first()
    )
    return getattr(getattr(membership, "organization", None), "facility", None)


def _resolve_patient_facility(patient: Patient) -> Any | None:
    """A Patient has no facility of its own, so this finds the one the event belongs to."""
    encounter = Encounter.objects.filter(patient=patient).order_by("-created_date").select_related("facility").first()
    if encounter:
        return encounter.facility
    return _registering_user_facility(patient)


_FACILITY_RESOLVERS[Patient] = _resolve_patient_facility
NOTIFICATION_CONTEXT_REGISTRY.register(PATIENT_CONTEXT_SLUG, PatientNotificationContext)


def display_patient_id(patient: Patient) -> str:
    """A configured instance identifier if the deployment has one, else the external id."""
    identifiers = getattr(patient, "instance_identifiers", None) or []
    for identifier in identifiers:
        if isinstance(identifier, dict) and identifier.get("value"):
            return str(identifier["value"])
    return str(patient.external_id)


def _fire_patient_event(patient: Patient, *, trigger_slug: str, action: str) -> None:
    now = timezone.localtime()
    fire_notification_event(
        trigger_slug=trigger_slug,
        title=f"Patient {action} — {patient.external_id}",
        related_object=patient,
        recipient=NotificationRecipientSpec(content_object=patient, phone_number=patient.phone_number),
        variable_values={
            "action": action,
            "header_action": action.capitalize(),
            "patient_id": display_patient_id(patient),
            "date_and_time": f"{humanize_date(now)}, {humanize_time(now)}",
        },
    )


@receiver(post_save, sender=Patient)
def on_patient_post_save(sender: type[Patient], instance: Patient, created: bool, **kwargs: Any) -> None:
    if not created:
        return
    _fire_patient_event(
        instance,
        trigger_slug=plugin_settings.PATIENT_TRIGGER_SLUGS["registered"],
        action="registered",
    )


pre_save.connect(track_previous_field("status"), sender=Encounter, weak=False)


@receiver(post_save, sender=Encounter)
def on_encounter_post_save(sender: type[Encounter], instance: Encounter, created: bool, **kwargs: Any) -> None:
    """Fires on the transition into `discharged` only -- re-saving an already-discharged."""
    if created:
        return

    previous_status = getattr(instance, "_previous_status", None)
    if previous_status == instance.status or instance.status != StatusChoices.discharged.value:
        return

    _fire_patient_event(
        instance.patient,
        trigger_slug=plugin_settings.PATIENT_TRIGGER_SLUGS["discharged"],
        action="discharged",
    )
