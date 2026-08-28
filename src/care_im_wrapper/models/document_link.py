from __future__ import annotations

import secrets
from typing import Any

from care.utils.models.base import BaseModel  # pyright: ignore[reportMissingImports]
from django.db import models
from django.utils import timezone

from care_im_wrapper.core.choices import Provider
from care_im_wrapper.settings import plugin_settings

TOKEN_BYTES = 32


class DocumentLinkObjectKind(models.TextChoices):
    REPORT_UPLOAD = "report_upload", "Report Upload"  # pyright: ignore[reportAssignmentType]
    FILE_UPLOAD = "file_upload", "File Upload"  # pyright: ignore[reportAssignmentType]
    # A clinical record rendered from its own data rather than a stored artifact; there is
    # no file behind it, so mint_read_url() does not apply.
    DIAGNOSTIC_REPORT = "diagnostic_report", "Diagnostic Report"  # pyright: ignore[reportAssignmentType]


STORED_FILE_OBJECT_KINDS = frozenset({DocumentLinkObjectKind.REPORT_UPLOAD, DocumentLinkObjectKind.FILE_UPLOAD})


class DocumentLink(BaseModel):
    """
    An unguessable, expiring capability referencing a generated/uploaded PDF.
    The token is the durable capability; presigned URLs are minted fresh per
    request via mint_read_url() and never persisted (see mint_read_url()).
    """

    token = models.CharField(max_length=64, unique=True, db_index=True)
    object_kind = models.CharField(max_length=20, choices=DocumentLinkObjectKind.choices)
    # ReportUpload.external_id or FileUpload.external_id -- no cross-app FK.
    object_external_id = models.UUIDField()
    # Display/audit label, e.g. "diagnostic_report" -- descriptive, not enforced.
    document_type = models.CharField(max_length=100)
    patient_external_id = models.UUIDField()
    provider = models.CharField(max_length=20, choices=Provider.choices, default=Provider.WHATSAPP)
    expires_at = models.DateTimeField()
    # Audit signal only, not an enforcement gate.
    access_count = models.PositiveIntegerField(default=0)  # pyright: ignore[reportArgumentType]

    class Meta:
        app_label = "care_im_wrapper"
        indexes = [
            models.Index(fields=["patient_external_id", "object_kind", "object_external_id"]),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.token:
            self.token = secrets.token_urlsafe(TOKEN_BYTES)
        super().save(*args, **kwargs)

    def is_valid(self) -> bool:
        return not self.deleted and timezone.now() < self.expires_at  # pyright: ignore[reportOperatorIssue]

    def resolve_file_object(self) -> Any:
        if self.object_kind not in STORED_FILE_OBJECT_KINDS:
            msg = f"DocumentLink of kind '{self.object_kind}' has no stored file behind it."
            raise ValueError(msg)

        if self.object_kind == DocumentLinkObjectKind.REPORT_UPLOAD:
            from care.emr.models.report.report_upload import ReportUpload  # pyright: ignore[reportMissingImports]

            return ReportUpload.objects.get(external_id=self.object_external_id)

        from care.emr.models.file_upload import FileUpload  # pyright: ignore[reportMissingImports]

        return FileUpload.objects.get(external_id=self.object_external_id)

    def mint_read_url(self) -> str:
        """Mints a fresh, short-TTL presigned GET URL. Never cache or persist the result --
        the token is the durable capability, this presign is deliberately ephemeral."""
        file_object = self.resolve_file_object()
        return file_object.files_manager.read_signed_url(
            file_object, duration=int(plugin_settings.DOCUMENT_PRESIGN_TTL_SECONDS)
        )
