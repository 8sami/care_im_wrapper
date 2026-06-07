from datetime import timedelta

from django.db import models
from django.utils import timezone


class ConversationSession(models.Model):
    class UserType(models.TextChoices):
        UNKNOWN = "unknown", "Unknown"
        PATIENT = "patient", "Patient"
        STAFF = "staff", "Staff"

    class State(models.TextChoices):
        NEW = "new", "New"
        AWAITING_YOB = "awaiting_yob", "Awaiting Year of Birth"
        AMBIGUOUS = "ambiguous", "Ambiguous"
        AUTHENTICATED = "authenticated", "Authenticated"
        COOLDOWN = "cooldown", "Cooldown"

    phone_number = models.CharField(max_length=20)
    provider = models.CharField(max_length=20, default="whatsapp")
    user_type = models.CharField(max_length=10, choices=UserType.choices, default=UserType.UNKNOWN)
    # IntegerField not FK — cross-package FK causes migration dependency issues
    user_id = models.IntegerField(null=True, blank=True)
    snapshot_name = models.CharField(max_length=255, blank=True, default="")
    snapshot_phone = models.CharField(max_length=20, blank=True, default="")
    state = models.CharField(max_length=20, choices=State.choices, default=State.NEW)
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    cooldown_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_seen_at = models.DateTimeField(auto_now=True)

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

    def increment_failed_attempt(self) -> None:
        self.failed_attempts += 1
        if self.failed_attempts >= 5:
            self.state = self.State.COOLDOWN
            self.cooldown_until = timezone.now() + timedelta(minutes=30)
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
