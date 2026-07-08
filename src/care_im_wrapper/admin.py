from care.emr.models.scheduling.booking import TokenBooking  # pyright: ignore[reportMissingImports]
from django import forms
from django.contrib import admin

from care_im_wrapper.core.sanitize import mask_phone_number
from care_im_wrapper.models.notification import (
    NotificationEvent,
    NotificationRecipient,
    NotificationStatus,
    NotificationTemplate,
    NotificationTrigger,
)


@admin.register(TokenBooking)
class TokenBookingAdmin(admin.ModelAdmin):
    """Demo/debug convenience: TokenBooking has no admin registration in care core. This won't be used in production."""

    list_display = ("external_id", "patient", "patient_phone", "patient_year_of_birth", "status", "booked_on")
    list_filter = ("status",)
    fields = ("external_id", "patient", "patient_phone", "patient_year_of_birth", "status", "booked_on")
    readonly_fields = ("external_id", "patient", "patient_phone", "patient_year_of_birth", "booked_on")

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        # Deferred import: circular import chain if loaded at admin.autodiscover() time.
        if db_field.name == "status":
            from care.emr.resources.scheduling.slot.spec import (  # pyright: ignore[reportMissingImports]
                BookingStatusChoices,
            )

            kwargs["widget"] = forms.Select(choices=[(c.value, c.value) for c in BookingStatusChoices])
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    @admin.display(description="Phone")
    def patient_phone(self, obj: TokenBooking) -> str:
        return str(obj.patient.phone_number)

    @admin.display(description="Year of birth")
    def patient_year_of_birth(self, obj: TokenBooking) -> str:
        return str(getattr(obj.patient, "year_of_birth", "") or "")


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
