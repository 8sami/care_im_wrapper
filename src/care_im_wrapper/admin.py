from django.contrib import admin

from care_im_wrapper.core.sanitize import mask_phone_number
from care_im_wrapper.models.document_link import DocumentLink
from care_im_wrapper.models.notification import (
    NotificationEvent,
    NotificationRecipient,
    NotificationStatus,
    NotificationTemplate,
    NotificationTrigger,
)

# Field names supplied by care.utils.models.base.BaseModel, shared by every
# NotificationTrigger/Template/Event/Recipient admin's readonly_fields.
_BASE_READONLY_FIELDS = ("external_id", "created_date", "modified_date", "deleted")
# created_by_id/updated_by_id are plugin-specific additions present on every model except
# NotificationRecipient (captured once at creation, never attributed to a staff editor).
_AUDITED_READONLY_FIELDS = (*_BASE_READONLY_FIELDS, "created_by_id", "updated_by_id")


@admin.register(NotificationTrigger)
class NotificationTriggerAdmin(admin.ModelAdmin):
    list_display = (
        "external_id",
        "name",
        "slug",
        "template_slug",
        "trigger_type",
        "is_active",
    )
    list_filter = ("trigger_type", "is_active")
    readonly_fields = (
        *_AUDITED_READONLY_FIELDS,
        "name",
        "slug",
        "description",
        "trigger_type",
        "is_active",
    )


@admin.register(NotificationTemplate)
class NotificationTemplateAdmin(admin.ModelAdmin):
    list_display = (
        "external_id",
        "name",
        "slug",
        "provider",
        "category",
        "approval_status",
        "is_active",
        "parameter_format",
    )
    list_filter = ("provider", "category", "approval_status", "parameter_format")
    readonly_fields = (
        *_AUDITED_READONLY_FIELDS,
        "name",
        "slug",
        "provider",
        "category",
        "approval_status",
        "payload",
        "language_code",
        "parameter_format",
    )


@admin.register(NotificationEvent)
class NotificationEventAdmin(admin.ModelAdmin):
    list_display = (
        "external_id",
        "title",
        "trigger",
        "template",
        "is_urgent",
        "created_date",
    )
    list_filter = ("trigger", "is_urgent")
    readonly_fields = (
        *_AUDITED_READONLY_FIELDS,
        "template",
        "trigger",
        "title",
        "description",
        "is_urgent",
        "variable_values",
        "related_object_content_type",
        "related_object_id",
        "facility_organization_cache",
    )


@admin.register(NotificationRecipient)
class NotificationRecipientAdmin(admin.ModelAdmin):
    list_display = (
        "external_id",
        "event",
        "recipient_phone",
        "tracking_id",
        "latest_status",
    )
    list_filter = ("latest_status", "provider")
    readonly_fields = (
        *_BASE_READONLY_FIELDS,
        "event",
        "recipient_content_type",
        "recipient_object_id",
        "phone_number",
        "provider",
        "tracking_id",
        "message_payload",
        "variable_overrides",
        "latest_status",
    )

    @admin.display(description="Phone")
    def recipient_phone(self, obj: NotificationRecipient) -> str:
        return mask_phone_number(str(obj.phone_number))


@admin.register(NotificationStatus)
class NotificationStatusAdmin(admin.ModelAdmin):
    list_display = (
        "external_id",
        "recipient",
        "state",
        "created_date",
    )
    list_filter = ("state",)
    readonly_fields = (
        "external_id",
        "created_date",
        "recipient",
        "state",
        "payload",
    )


@admin.register(DocumentLink)
class DocumentLinkAdmin(admin.ModelAdmin):
    # token stays off list_display -- it's a bearer capability.
    list_display = (
        "external_id",
        "document_type",
        "object_kind",
        "provider",
        "expires_at",
        "access_count",
    )
    list_filter = ("object_kind", "provider", "document_type")
    readonly_fields = (
        *_BASE_READONLY_FIELDS,
        "token",
        "object_kind",
        "object_external_id",
        "document_type",
        "patient_external_id",
        "provider",
        "expires_at",
        "access_count",
    )
