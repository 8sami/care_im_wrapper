import uuid
from collections.abc import Callable
from typing import Any

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres.fields import ArrayField
from django.db import models

from care_im_wrapper.models.conversation_session import ConversationSession


class SoftDeleteManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(deleted=False)


class TriggerType(models.TextChoices):
    SIGNAL = "signal", "Signal"  # pyright: ignore[reportAssignmentType]
    MANUAL = "manual", "Manual"  # pyright: ignore[reportAssignmentType]


class NotificationTrigger(models.Model):
    external_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True)
    created_date = models.DateTimeField(auto_now_add=True, db_index=True)
    modified_date = models.DateTimeField(auto_now=True)
    deleted = models.BooleanField(default=False, db_index=True)  # pyright: ignore[reportArgumentType]
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

    objects = SoftDeleteManager()

    class Meta:
        app_label = "care_im_wrapper"
        indexes = [
            models.Index(fields=["trigger_type", "is_active"]),
        ]

    def delete(self, *args: Any, **kwargs: Any) -> None:  # pyright: ignore[reportIncompatibleMethodOverride]
        self.deleted = True
        self.save(update_fields=["deleted"])


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


class NotificationTemplate(models.Model):
    external_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True)
    created_date = models.DateTimeField(auto_now_add=True, db_index=True)
    modified_date = models.DateTimeField(auto_now=True)
    deleted = models.BooleanField(default=False, db_index=True)  # pyright: ignore[reportArgumentType]
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

    objects = SoftDeleteManager()

    class Meta:
        app_label = "care_im_wrapper"
        indexes = [
            models.Index(fields=["provider", "approval_status"]),
        ]

    def delete(self, *args: Any, **kwargs: Any) -> None:  # pyright: ignore[reportIncompatibleMethodOverride]
        self.deleted = True
        self.save(update_fields=["deleted"])


class NotificationEvent(models.Model):
    external_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True)
    created_date = models.DateTimeField(auto_now_add=True, db_index=True)
    modified_date = models.DateTimeField(auto_now=True)
    deleted = models.BooleanField(default=False, db_index=True)  # pyright: ignore[reportArgumentType]
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
    facility_organization_cache = ArrayField(models.IntegerField(), default=list)

    objects = SoftDeleteManager()

    class Meta:
        app_label = "care_im_wrapper"
        indexes = [
            models.Index(fields=["related_object_content_type", "related_object_id"]),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        related_object = self.related_object
        if related_object is None:
            self.facility_organization_cache = []
        else:
            facility = _resolve_facility(related_object)
            if facility is None:
                self.facility_organization_cache = []
            else:
                from care.emr.models.organization import FacilityOrganization  # pyright: ignore[reportMissingImports]

                facility_root_org = FacilityOrganization.objects.filter(org_type="root", facility=facility).first()
                orgs = set()
                if facility_root_org:
                    orgs = orgs.union({facility_root_org.id})
                self.facility_organization_cache = list(orgs)
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> None:  # pyright: ignore[reportIncompatibleMethodOverride]
        self.deleted = True
        self.save(update_fields=["deleted"])


class NotificationStatusState(models.TextChoices):
    SENT = "sent", "Sent"  # pyright: ignore[reportAssignmentType]
    DELIVERED = "delivered", "Delivered"  # pyright: ignore[reportAssignmentType]
    READ = "read", "Read"  # pyright: ignore[reportAssignmentType]
    FAILED = "failed", "Failed"  # pyright: ignore[reportAssignmentType]


class NotificationRecipient(models.Model):
    external_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True)
    created_date = models.DateTimeField(auto_now_add=True, db_index=True)
    modified_date = models.DateTimeField(auto_now=True)
    deleted = models.BooleanField(default=False, db_index=True)  # pyright: ignore[reportArgumentType]
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

    objects = SoftDeleteManager()

    class Meta:
        app_label = "care_im_wrapper"
        indexes = [
            models.Index(fields=["tracking_id"]),
            models.Index(fields=["recipient_content_type", "recipient_object_id"]),
            models.Index(fields=["event", "latest_status"]),
        ]

    def delete(self, *args: Any, **kwargs: Any) -> None:  # pyright: ignore[reportIncompatibleMethodOverride]
        self.deleted = True
        self.save(update_fields=["deleted"])


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
