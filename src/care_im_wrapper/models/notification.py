import uuid
from typing import Any

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

    objects = SoftDeleteManager()

    class Meta:
        app_label = "care_im_wrapper"
        indexes = [
            models.Index(fields=["trigger_type", "is_active"]),
        ]

    def delete(self, *args: Any, **kwargs: Any) -> None:  # pyright: ignore[reportIncompatibleMethodOverride]
        self.deleted = True
        self.save(update_fields=["deleted"])


class NotificationCategory(models.TextChoices):
    MARKETING = "marketing", "Marketing"  # pyright: ignore[reportAssignmentType]
    UTILITY = "utility", "Utility"  # pyright: ignore[reportAssignmentType]
    AUTHENTICATION = "authentication", "Authentication"  # pyright: ignore[reportAssignmentType]


class TemplateApprovalStatus(models.TextChoices):
    PENDING = "pending", "Pending"  # pyright: ignore[reportAssignmentType]
    ACTIVE = "active", "Active"  # pyright: ignore[reportAssignmentType]
    REJECTED = "rejected", "Rejected"  # pyright: ignore[reportAssignmentType]
    DISABLED = "disabled", "Disabled"  # pyright: ignore[reportAssignmentType]


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

    objects = SoftDeleteManager()

    class Meta:
        app_label = "care_im_wrapper"
        indexes = [
            models.Index(fields=["provider", "approval_status"]),
        ]

    def delete(self, *args: Any, **kwargs: Any) -> None:  # pyright: ignore[reportIncompatibleMethodOverride]
        self.deleted = True
        self.save(update_fields=["deleted"])
