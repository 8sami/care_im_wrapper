from __future__ import annotations

from datetime import datetime, timedelta

from django.db import models
from django.utils import timezone

from care_im_wrapper.settings import plugin_settings


class ConversationSession(models.Model):
    class UserType(models.TextChoices):
        UNKNOWN = "unknown", "Unknown"  # pyright: ignore[reportAssignmentType]
        PATIENT = "patient", "Patient"  # pyright: ignore[reportAssignmentType]
        STAFF = "staff", "Staff"  # pyright: ignore[reportAssignmentType]

    class Provider(models.TextChoices):
        WHATSAPP = "whatsapp", "WhatsApp"  # pyright: ignore[reportAssignmentType]

    class State(models.TextChoices):
        NEW = "new", "New"  # pyright: ignore[reportAssignmentType]
        AWAITING_YOB = "awaiting_yob", "Awaiting Year of Birth"  # pyright: ignore[reportAssignmentType]
        AMBIGUOUS = "ambiguous", "Ambiguous"  # pyright: ignore[reportAssignmentType]
        AUTHENTICATED = "authenticated", "Authenticated"  # pyright: ignore[reportAssignmentType]
        COOLDOWN = "cooldown", "Cooldown"  # pyright: ignore[reportAssignmentType]

    phone_number = models.CharField(max_length=20)
    provider = models.CharField(max_length=20, choices=Provider.choices, default=Provider.WHATSAPP)
    user_type = models.CharField(max_length=10, choices=UserType.choices, default=UserType.UNKNOWN)
    # IntegerField not FK — cross-package FK causes migration dependency issues
    user_id = models.IntegerField(null=True, blank=True)
    snapshot_name = models.CharField(max_length=255, blank=True, default="")
    snapshot_phone = models.CharField(max_length=20, blank=True, default="")
    candidates = models.JSONField(default=list, blank=True)
    state = models.CharField(max_length=20, choices=State.choices, default=State.NEW)
    failed_attempts = models.PositiveSmallIntegerField(default=0)  # pyright: ignore[reportArgumentType]
    cooldown_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "care_im_wrapper"
        constraints = [
            models.UniqueConstraint(
                fields=["phone_number", "provider"],
                name="uq_session_phone_provider",
            )
        ]
        indexes = [
            models.Index(fields=["phone_number", "provider"]),
            models.Index(fields=["state"]),
        ]

    def is_in_cooldown(self) -> bool:
        if self.state != self.State.COOLDOWN:
            return False
        if self.cooldown_until and self.cooldown_until > timezone.now():
            return True

        # Expired
        self.state = self.State.NEW
        self.failed_attempts = 0
        self.cooldown_until = None
        self.save(update_fields=["state", "failed_attempts", "cooldown_until"])
        return False

    def get_cooldown_remaining_minutes(self) -> int | None:
        if self.state == self.State.COOLDOWN and self.cooldown_until:
            remaining_dt = self.cooldown_until

            if isinstance(remaining_dt, datetime):
                diff = remaining_dt - timezone.now()
                if diff.total_seconds() > 0:
                    return max(1, int(diff.total_seconds() // 60))
        return None

    def increment_failed_attempt(self) -> None:
        self.failed_attempts += 1  # pyright: ignore[reportOperatorIssue]
        if self.failed_attempts >= plugin_settings.MAX_FAILED_ATTEMPTS:
            self.state = self.State.COOLDOWN
            self.cooldown_until = timezone.now() + timedelta(minutes=plugin_settings.COOLDOWN_MINUTES)
        self.save(update_fields=["state", "failed_attempts", "cooldown_until"])

    def authenticate(self, user_type: str, user_id: int, name: str, phone: str) -> None:
        self.state = self.State.AUTHENTICATED
        self.user_type = user_type
        self.user_id = user_id
        self.snapshot_name = name
        self.snapshot_phone = phone
        self.failed_attempts = 0
        self.cooldown_until = None
        self.save(
            update_fields=[
                "state",
                "user_type",
                "user_id",
                "snapshot_name",
                "snapshot_phone",
                "failed_attempts",
                "cooldown_until",
            ]
        )

    def logout(self) -> None:
        self.state = self.State.NEW
        self.user_type = self.UserType.UNKNOWN
        self.user_id = None
        self.snapshot_name = ""
        self.snapshot_phone = ""
        self.failed_attempts = 0
        self.cooldown_until = None
        self.candidates = []
        self.save(
            update_fields=[
                "state",
                "user_type",
                "user_id",
                "snapshot_name",
                "snapshot_phone",
                "failed_attempts",
                "cooldown_until",
                "candidates",
            ]
        )
