import uuid
from collections.abc import Callable
from typing import Any

from care.utils.models.base import BaseModel  # pyright: ignore[reportMissingImports]
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from care_im_wrapper.models.conversation_session import ConversationSession


class TriggerType(models.TextChoices):
    SIGNAL = "signal", "Signal"  # pyright: ignore[reportAssignmentType]
    MANUAL = "manual", "Manual"  # pyright: ignore[reportAssignmentType]


class NotificationTrigger(BaseModel):
    # care.users.User.id; plain IntegerField (not a FK) to avoid a cross-app migration dependency.
    created_by_id = models.IntegerField(null=True, blank=True)
    updated_by_id = models.IntegerField(null=True, blank=True)
    name = models.CharField(max_length=255)
    slug = models.CharField(max_length=100, unique=True)
    description = models.TextField(null=True, blank=True)
    trigger_type = models.CharField(max_length=20, choices=TriggerType.choices)
    is_active = models.BooleanField(default=True)  # pyright: ignore[reportArgumentType]
    # Which NotificationTemplate.slug this trigger renders, decoupled from this trigger's own slug.
    template_slug = models.CharField(max_length=100)
    # Base context merged into NotificationEvent.variable_values (handler values win on collision).
    default_variable_values = models.JSONField(null=True, blank=True)
    # Names the context class in NOTIFICATION_CONTEXT_REGISTRY for this trigger's
    # related_object, driving the variable_mapping field picker. Blank = no picker.
    context_slug = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        app_label = "care_im_wrapper"
        indexes = [
            models.Index(fields=["trigger_type", "is_active"]),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.context_slug:
            # Lazy import: keeps care.emr out of the model module's import path at app load.
            from django.core.exceptions import ValidationError

            from care_im_wrapper.reports.context_builders import NOTIFICATION_CONTEXT_REGISTRY

            if self.context_slug not in NOTIFICATION_CONTEXT_REGISTRY:
                raise ValidationError(
                    {
                        "context_slug": f"Unknown context_slug '{self.context_slug}'. "
                        f"Registered: {sorted(NOTIFICATION_CONTEXT_REGISTRY.slugs())}."
                    }
                )
        super().save(*args, **kwargs)


# Registered by handlers/booking.py and other future related-object handlers,
# so this module never needs to import care.emr models directly.
_FACILITY_RESOLVERS: dict[type, Callable[[Any], Any | None]] = {}


def _resolve_facility(related_object: Any) -> Any | None:
    resolver = _FACILITY_RESOLVERS.get(type(related_object))
    if resolver is None:
        return None
    return resolver(related_object)


class NotificationCategory(models.TextChoices):
    MARKETING = "marketing", "Marketing"  # pyright: ignore[reportAssignmentType]
    UTILITY = "utility", "Utility"  # pyright: ignore[reportAssignmentType]
    AUTHENTICATION = "authentication", "Authentication"  # pyright: ignore[reportAssignmentType]


class TemplateApprovalStatus(models.TextChoices):
    PENDING = "pending", "Pending"  # pyright: ignore[reportAssignmentType]
    ACTIVE = "active", "Active"  # pyright: ignore[reportAssignmentType]
    REJECTED = "rejected", "Rejected"  # pyright: ignore[reportAssignmentType]
    DISABLED = "disabled", "Disabled"  # pyright: ignore[reportAssignmentType]


class TemplateParameterFormat(models.TextChoices):
    """
    Which of Meta's two mutually-exclusive body-parameter schemes this template
    uses. A given approved WhatsApp template is created with exactly one of these —
    never a mix — so this is a per-template setting, not something inferred at
    dispatch time.
    """

    POSITIONAL = "positional", "Positional ({{1}}, {{2}}, ...)"  # pyright: ignore[reportAssignmentType]
    NAMED = "named", "Named ({{patient_name}}, ...)"  # pyright: ignore[reportAssignmentType]


class NotificationTemplate(BaseModel):
    created_by_id = models.IntegerField(null=True, blank=True)
    updated_by_id = models.IntegerField(null=True, blank=True)
    name = models.CharField(max_length=255)
    slug = models.CharField(max_length=100, unique=True)
    provider = models.CharField(
        max_length=20,
        choices=ConversationSession.Provider.choices,
        default=ConversationSession.Provider.WHATSAPP,
    )
    category = models.CharField(max_length=20, choices=NotificationCategory.choices)
    approval_status = models.CharField(
        max_length=20,
        choices=TemplateApprovalStatus.choices,
        default=TemplateApprovalStatus.PENDING,
    )
    is_active = models.BooleanField(default=True)  # pyright: ignore[reportArgumentType]
    language_code = models.CharField(max_length=10, null=True, blank=True)
    payload = models.JSONField(null=True, blank=True)
    variable_mapping = models.JSONField(null=True, blank=True)
    # Synced from Meta; defaults to "positional" per Meta's own documented behavior.
    parameter_format = models.CharField(
        max_length=20,
        choices=TemplateParameterFormat.choices,
        default=TemplateParameterFormat.POSITIONAL,
    )

    class Meta:
        app_label = "care_im_wrapper"
        indexes = [
            models.Index(fields=["provider", "approval_status"]),
        ]


class NotificationEvent(BaseModel):
    # care.users.User.id of staff member. NULL for automatic signal-triggered events.
    created_by_id = models.IntegerField(null=True, blank=True)
    updated_by_id = models.IntegerField(null=True, blank=True)
    template = models.ForeignKey(NotificationTemplate, on_delete=models.PROTECT)
    trigger = models.ForeignKey(NotificationTrigger, on_delete=models.PROTECT)
    title = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    is_urgent = models.BooleanField(default=False)  # pyright: ignore[reportArgumentType]
    variable_values = models.JSONField(null=True, blank=True)
    related_object_content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, null=True, blank=True)
    related_object_id = models.PositiveIntegerField(null=True, blank=True)
    related_object = GenericForeignKey("related_object_content_type", "related_object_id")
    # care.facility.Facility.id the event belongs to, resolved from related_object at save
    # time. IntegerField not FK, for the reason ConversationSession.user_id gives.
    # An event is scoped to a whole facility, never to one department within it, so this --
    # not a set of organization ids -- is what authorization reads.
    facility_id = models.IntegerField(null=True, blank=True, db_index=True)

    class Meta:
        app_label = "care_im_wrapper"
        indexes = [
            models.Index(fields=["related_object_content_type", "related_object_id"]),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        related_object = self.related_object
        facility = None if related_object is None else _resolve_facility(related_object)
        self.facility_id = None if facility is None else facility.id
        super().save(*args, **kwargs)


class NotificationStatusState(models.TextChoices):
    SENT = "sent", "Sent"  # pyright: ignore[reportAssignmentType]
    DELIVERED = "delivered", "Delivered"  # pyright: ignore[reportAssignmentType]
    READ = "read", "Read"  # pyright: ignore[reportAssignmentType]
    FAILED = "failed", "Failed"  # pyright: ignore[reportAssignmentType]


class NotificationRecipient(BaseModel):
    event = models.ForeignKey(NotificationEvent, on_delete=models.CASCADE, related_name="recipients")
    recipient_content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    recipient_object_id = models.PositiveIntegerField()
    recipient = GenericForeignKey("recipient_content_type", "recipient_object_id")
    # Captured at creation time, immutable afterward, even if the recipient's number later changes.
    phone_number = models.CharField(max_length=20)
    provider = models.CharField(
        max_length=20,
        choices=ConversationSession.Provider.choices,
        default=ConversationSession.Provider.WHATSAPP,
    )
    tracking_id = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    message_payload = models.JSONField(null=True, blank=True)
    variable_overrides = models.JSONField(null=True, blank=True)
    # Pure cache written as a side effect of inserting a NotificationStatus row; None = not dispatched.
    latest_status = models.CharField(max_length=20, null=True, blank=True, choices=NotificationStatusState.choices)
    # Dispatch claim: set by the worker before sending so the sweep won't re-queue a recipient
    # already in flight (latest_status is set only after success, too late to be the claim).
    # A stale claim from a dead worker is reclaimed by the sweep.
    dispatch_started_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "care_im_wrapper"
        indexes = [
            models.Index(fields=["tracking_id"]),
            models.Index(fields=["recipient_content_type", "recipient_object_id"]),
            models.Index(fields=["event", "latest_status"]),
            # Drives the sweep's "unsent and unclaimed (or stale)" scan.
            models.Index(fields=["latest_status", "dispatch_started_at"]),
        ]


class NotificationStatus(models.Model):
    external_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True)
    created_date = models.DateTimeField(auto_now_add=True, db_index=True)
    recipient = models.ForeignKey(NotificationRecipient, on_delete=models.CASCADE, related_name="status_events")
    state = models.CharField(max_length=20, choices=NotificationStatusState.choices)
    payload = models.JSONField(null=True, blank=True)

    objects = models.Manager()

    class Meta:
        app_label = "care_im_wrapper"
        indexes = [
            models.Index(fields=["recipient", "state"]),
            models.Index(fields=["created_date"]),
        ]
