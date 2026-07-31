from __future__ import annotations

from datetime import datetime, timedelta

from django.db import models
from django.utils import timezone

from care_im_wrapper.core.choices import Provider
from care_im_wrapper.settings import plugin_settings


class ConversationSession(models.Model):
    class UserType(models.TextChoices):
        UNKNOWN = "unknown", "Unknown"  # pyright: ignore[reportAssignmentType]
        PATIENT = "patient", "Patient"  # pyright: ignore[reportAssignmentType]
        STAFF = "staff", "Staff"  # pyright: ignore[reportAssignmentType]

    Provider = Provider

    class State(models.TextChoices):
        NEW = "new", "New"  # pyright: ignore[reportAssignmentType]
        AWAITING_YOB = "awaiting_yob", "Awaiting Year of Birth"  # pyright: ignore[reportAssignmentType]
        AMBIGUOUS = "ambiguous", "Ambiguous"  # pyright: ignore[reportAssignmentType]
        AUTHENTICATED = "authenticated", "Authenticated"  # pyright: ignore[reportAssignmentType]
        COOLDOWN = "cooldown", "Cooldown"  # pyright: ignore[reportAssignmentType]
        AWAITING_PATIENT_SEARCH = "awaiting_patient_search", "Awaiting Patient Search"  # pyright: ignore[reportAssignmentType]
        SELECTING_PATIENT = "selecting_patient", "Selecting Patient"  # pyright: ignore[reportAssignmentType]
        SELECTING_DOCUMENT = "selecting_document", "Selecting Document"  # pyright: ignore[reportAssignmentType]

    phone_number = models.CharField(max_length=20)
    provider = models.CharField(max_length=20, choices=Provider.choices, default=Provider.WHATSAPP)
    user_type = models.CharField(max_length=10, choices=UserType.choices, default=UserType.UNKNOWN)
    # IntegerField not FK — cross-package FK causes migration dependency issues
    user_id = models.IntegerField(null=True, blank=True)
    active_patient_external_id = models.CharField(max_length=255, blank=True, null=True)
    snapshot_name = models.CharField(max_length=255, blank=True, default="")
    snapshot_phone = models.CharField(max_length=20, blank=True, default="")
    candidates = models.JSONField(default=list, blank=True)
    state = models.CharField(max_length=30, choices=State.choices, default=State.NEW)
    failed_attempts = models.PositiveSmallIntegerField(default=0)  # pyright: ignore[reportArgumentType]
    cooldown_until = models.DateTimeField(null=True, blank=True)
    data_menu_choice = models.CharField(max_length=8, blank=True, default="")
    data_offsets = models.JSONField(default=list, blank=True)
    # Records the current page displayed after trimming, so "next" knows where it ended.
    data_shown = models.PositiveIntegerField(default=0)  # pyright: ignore[reportArgumentType]
    # The staff lookup query, so its results can be re-run a page along without retyping.
    search_query = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_active_at = models.DateTimeField(default=timezone.now)

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

    def record_activity(self) -> None:
        """Call once per inbound turn, before dispatch. Logs out a session idle past."""
        if self.state != self.State.COOLDOWN:
            idle_for = timezone.now() - self.last_active_at
            if idle_for.total_seconds() > int(plugin_settings.SESSION_IDLE_TIMEOUT_SECONDS):
                self.logout()
        self.last_active_at = timezone.now()
        self.save(update_fields=["last_active_at"])

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
        if self.failed_attempts >= int(plugin_settings.MAX_FAILED_ATTEMPTS):
            self.state = self.State.COOLDOWN
            self.cooldown_until = timezone.now() + timedelta(minutes=int(plugin_settings.COOLDOWN_MINUTES))
        self.save(update_fields=["state", "failed_attempts", "cooldown_until"])

    def authenticate(self, user_type: ConversationSession.UserType, user_id: int, name: str, phone: str) -> None:
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
        self.active_patient_external_id = None
        self.snapshot_name = ""
        self.snapshot_phone = ""
        self.failed_attempts = 0
        self.cooldown_until = None
        self.candidates = []
        self.data_menu_choice = ""
        self.data_offsets = []
        self.search_query = ""
        self.save(
            update_fields=[
                "search_query",
                "state",
                "user_type",
                "user_id",
                "active_patient_external_id",
                "snapshot_name",
                "snapshot_phone",
                "failed_attempts",
                "cooldown_until",
                "candidates",
                "data_menu_choice",
                "data_offsets",
            ],
        )

    def open_data_list(self, menu_choice: str) -> None:
        """Start reading a menu option from the top."""
        self.data_menu_choice = menu_choice
        self.data_offsets = []
        self.data_shown = 0
        self.save(update_fields=["data_menu_choice", "data_offsets", "data_shown"])

    def record_shown(self, shown: int) -> None:
        """Remember the size of the page just rendered, so `advance_page` can step exactly."""
        shown = max(0, int(shown))
        if self.data_shown != shown:
            self.data_shown = shown
            self.save(update_fields=["data_shown"])

    def next_offset(self) -> int:
        """Where the page after the current one begins."""
        from care_im_wrapper.data.pagination import current_offset

        return current_offset(self) + int(self.data_shown or 0)

    def advance_page(self, next_offset: int) -> None:
        """Move forward to the page starting at `next_offset`, remembering where this one."""
        self.data_offsets = [*(self.data_offsets or []), max(0, int(next_offset))]
        self.save(update_fields=["data_offsets"])

    def back_page(self) -> None:
        self.data_offsets = (self.data_offsets or [])[:-1]
        self.save(update_fields=["data_offsets"])

    @property
    def data_page(self) -> int:
        """0-based index of the current page, for display."""
        return len(self.data_offsets or [])

    def reset_data_page(self) -> None:
        """Clears paging state -- on logout, on switching patient, or on leaving a list."""
        if self.data_menu_choice or self.data_offsets:
            self.data_menu_choice = ""
            self.data_offsets = []
            self.save(update_fields=["data_menu_choice", "data_offsets"])

    def open_search(self, query: str) -> None:
        """Records the patient-lookup query and restarts its results from the top."""
        self.search_query = query
        self.data_offsets = []
        self.data_shown = 0
        self.save(update_fields=["search_query", "data_offsets", "data_shown"])
