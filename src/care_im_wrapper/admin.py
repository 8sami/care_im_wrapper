from django.contrib import admin

from care_im_wrapper.core.sanitize import mask_phone_number
from care_im_wrapper.models.notification import (
    NotificationEvent,
    NotificationRecipient,
    NotificationStatus,
    NotificationTemplate,
    NotificationTrigger,
)


@admin.register(NotificationTrigger)
class NotificationTriggerAdmin(admin.ModelAdmin):
    list_display = (
        "external_id",
        "name",
        "slug",
        "trigger_type",
        "is_active",
    )
    list_filter = ("trigger_type", "is_active")
    readonly_fields = (
        "external_id",
        "created_date",
        "modified_date",
        "deleted",
        "created_by_id",
        "updated_by_id",
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
        "external_id",
        "created_date",
        "modified_date",
        "deleted",
        "created_by_id",
        "updated_by_id",
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
        "external_id",
        "created_date",
        "modified_date",
        "deleted",
        "created_by_id",
        "updated_by_id",
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
        "external_id",
        "created_date",
        "modified_date",
        "deleted",
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
