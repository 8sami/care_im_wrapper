import datetime
from typing import Any

from care.emr.resources.base import EMRResource  # pyright: ignore[reportMissingImports]
from care.utils.shortcuts import get_object_or_404  # pyright: ignore[reportMissingImports]
from pydantic import UUID4

from care_im_wrapper.models.notification import (
    NotificationEvent,
    NotificationRecipient,
    NotificationStatus,
    NotificationTemplate,
    NotificationTrigger,
)


class NotificationTemplateReadSpec(EMRResource):
    __model__ = NotificationTemplate
    __exclude__ = []

    id: UUID4 | None = None
    name: str
    slug: str
    provider: str
    category: str
    approval_status: str
    is_active: bool
    language_code: str | None = None
    payload: dict[str, Any] | None = None
    variable_mapping: dict[str, Any] | None = None
    # Synced from Meta, not staff-editable — exposed so staff know the shape to submit via set_variable_mapping.
    parameter_format: str
    created_date: datetime.datetime | None = None
    modified_date: datetime.datetime | None = None


class NotificationTemplateWriteSpec(EMRResource):
    __model__ = NotificationTemplate
    # Only these two fields declared — de_serialize can never touch the other, sync-only fields.
    __exclude__ = []

    is_active: bool
    variable_mapping: dict[str, Any] | None = None


class NotificationTriggerReadSpec(EMRResource):
    __model__ = NotificationTrigger
    __exclude__ = []

    id: UUID4 | None = None
    name: str
    slug: str
    description: str | None = None
    trigger_type: str
    is_active: bool
    context_slug: str | None = None


def _resolve_recipient_name(recipient: NotificationRecipient) -> str | None:
    target = recipient.recipient
    if target is None:
        return None
    if recipient.recipient_content_type.model == "patient":
        return getattr(target, "name", None) or None
    if recipient.recipient_content_type.model == "user":
        return getattr(target, "full_name", None) or None
    return None


class NotificationRecipientReadSpec(EMRResource):
    __model__ = NotificationRecipient
    __exclude__ = ["phone_number"]

    id: UUID4 | None = None
    event_id: UUID4
    recipient_phone: str
    recipient_name: str | None = None
    recipient_type: str | None = None
    provider: str
    tracking_id: str | None = None
    latest_status: str | None = None
    status_history: list[dict[str, Any]] = []
    created_date: datetime.datetime | None = None

    # message_payload/variable_overrides omitted: debugging-only raw payloads, not needed for a patient-facing view.

    @classmethod
    def perform_extra_serialization(cls, mapping, obj, *args, **kwargs):
        super().perform_extra_serialization(mapping, obj, *args, **kwargs)
        mapping["event_id"] = obj.event.external_id
        mapping["recipient_phone"] = obj.phone_number
        mapping["recipient_type"] = obj.recipient_content_type.model
        mapping["recipient_name"] = _resolve_recipient_name(obj)
        mapping["status_history"] = [
            {"state": status.state, "created_date": status.created_date.isoformat()}
            for status in sorted(obj.status_events.all(), key=lambda status: status.created_date)
        ]


class NotificationStatusReadSpec(EMRResource):
    __model__ = NotificationStatus
    __exclude__ = []

    id: UUID4 | None = None
    recipient_id: UUID4
    state: str
    payload: dict[str, Any] | None = None
    created_date: datetime.datetime | None = None

    @classmethod
    def perform_extra_serialization(cls, mapping, obj, *args, **kwargs):
        super().perform_extra_serialization(mapping, obj, *args, **kwargs)
        mapping["recipient_id"] = obj.recipient.external_id


class NotificationEventReadSpec(EMRResource):
    __model__ = NotificationEvent
    __exclude__ = []

    id: UUID4 | None = None
    trigger_id: UUID4
    template_id: UUID4
    title: str
    description: str | None = None
    is_urgent: bool
    variable_values: dict[str, Any] | None = None
    # Staff member who created a manual event; None for automatic signal-triggered events.
    created_by: dict | None = None
    created_date: datetime.datetime | None = None
    recipients: list[NotificationRecipientReadSpec] = []

    @classmethod
    def perform_extra_serialization(cls, mapping, obj, *args, **kwargs):
        super().perform_extra_serialization(mapping, obj, *args, **kwargs)
        mapping["trigger_id"] = obj.trigger.external_id
        mapping["template_id"] = obj.template.external_id
        mapping["recipients"] = [NotificationRecipientReadSpec.serialize(r) for r in obj.recipients.all()]
        cls.serialize_audit_users(mapping, obj)


class NotificationEventWriteSpec(EMRResource):
    __model__ = NotificationEvent
    __exclude__ = []

    title: str
    description: str | None = None
    is_urgent: bool = False
    variable_values: dict[str, Any] | None = None
    # Write-only lookup keys, resolved to trigger/template FKs in de_serialize.
    trigger_slug: str
    template_slug: str
    # Write-only, consumed by the viewset's perform_create to build NotificationRecipient rows.
    recipient_patient_ids: list[UUID4] = []
    recipient_user_ids: list[UUID4] = []

    def de_serialize(self, obj=None, partial=False):
        obj = super().de_serialize(obj=obj, partial=partial)
        obj.trigger = get_object_or_404(NotificationTrigger, slug=self.trigger_slug)
        obj.template = get_object_or_404(NotificationTemplate, slug=self.template_slug)
        # Stashed for perform_create, which only receives the model instance, not this spec.
        obj._recipient_patient_ids = self.recipient_patient_ids  # noqa: SLF001  # pyright: ignore[reportAttributeAccessIssue]
        obj._recipient_user_ids = self.recipient_user_ids  # noqa: SLF001  # pyright: ignore[reportAttributeAccessIssue]
        return obj
