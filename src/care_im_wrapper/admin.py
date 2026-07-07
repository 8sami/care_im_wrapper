from django.contrib import admin

from care_im_wrapper.models.notification import (
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
    )
    list_filter = ("provider", "category", "approval_status")
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
    )
