from django.db import migrations

# Slug of the TokenBooking context registered in NOTIFICATION_CONTEXT_REGISTRY
# (handlers/booking.py: BOOKING_CONTEXT_SLUG), matching the appointment triggers 0012 set.
BOOKING_CONTEXT_SLUG = "token_booking"

# The same template the existing appointment triggers render; each supplies only `status`.
TEMPLATE_SLUG = "appointment_update"

TRIGGERS = [
    {
        "slug": "appointment_no_show",
        "name": "Appointment No Show",
        "description": "Fires when a TokenBooking transitions to the 'noshow' status.",
        "status": "marked as a no-show",
    },
    {
        "slug": "appointment_checked_in",
        "name": "Appointment Checked In",
        "description": "Fires when a TokenBooking transitions to the 'checked_in' status.",
        "status": "checked in",
    },
    {
        "slug": "appointment_in_consultation",
        "name": "Appointment In Consultation",
        "description": "Fires when a TokenBooking transitions to the 'in_consultation' status.",
        "status": "marked as in consultation",
    },
    {
        "slug": "appointment_fulfilled",
        "name": "Appointment Fulfilled",
        "description": "Fires when a TokenBooking transitions to the 'fulfilled' status.",
        "status": "fulfilled",
    },
]


def seed_triggers(apps, schema_editor):
    NotificationTrigger = apps.get_model("care_im_wrapper", "NotificationTrigger")
    for trigger in TRIGGERS:
        NotificationTrigger.objects.get_or_create(
            slug=trigger["slug"],
            defaults={
                "name": trigger["name"],
                "trigger_type": "signal",
                "description": trigger["description"],
                "template_slug": TEMPLATE_SLUG,
                "context_slug": BOOKING_CONTEXT_SLUG,
                "default_variable_values": {"status": trigger["status"]},
            },
        )


def remove_triggers(apps, schema_editor):
    NotificationTrigger = apps.get_model("care_im_wrapper", "NotificationTrigger")
    NotificationTrigger.objects.filter(slug__in=[trigger["slug"] for trigger in TRIGGERS]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("care_im_wrapper", "0026_alter_notificationtrigger_trigger_type"),
    ]

    operations = [
        migrations.RunPython(seed_triggers, remove_triggers),
    ]
