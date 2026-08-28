import datetime
from typing import Any

from care.emr.resources.base import EMRResource  # pyright: ignore[reportMissingImports]
from pydantic import UUID4

from care_im_wrapper.core.sanitize import mask_phone_number
from care_im_wrapper.models.notification import (
    NotificationEvent,
    NotificationRecipient,
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
    # Resolved values actually sent; the raw inputs in message_payload stay omitted.
    sent_parameters: dict[str, str] = {}

    @classmethod
    def perform_extra_serialization(cls, mapping, obj, *args, **kwargs):
        super().perform_extra_serialization(mapping, obj, *args, **kwargs)
        mapping["event_id"] = obj.event.external_id
        # Masked to match every other surface (admin, patient lookup, patient summary);
        # the delivery log identifies a recipient by name, not by a dialable number.
        mapping["recipient_phone"] = mask_phone_number(obj.phone_number)
        mapping["recipient_type"] = obj.recipient_content_type.model
        mapping["recipient_name"] = _resolve_recipient_name(obj)
        mapping["sent_parameters"] = (obj.message_payload or {}).get("sent_parameters", {})
        mapping["status_history"] = [
            {
                "state": status.state,
                "created_date": status.created_date.isoformat(),
                # Raw provider error body or dispatch exception on failures; null otherwise.
                "payload": status.payload,
            }
            for status in sorted(obj.status_events.all(), key=lambda status: status.created_date)
        ]


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
    # Always None: events are created by signal handlers, never by a user. Kept so the
    # audit-user shape stays consistent with every other EMR read spec.
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
