from django.db import migrations

TRIGGERS = [
    {
        "slug": "appointment_confirmed",
        "name": "Appointment Confirmed",
        "trigger_type": "signal",
        "description": "Fires when a TokenBooking transitions to the 'booked' status.",
    },
    {
        "slug": "appointment_cancelled",
        "name": "Appointment Cancelled",
        "trigger_type": "signal",
        "description": "Fires when a TokenBooking transitions to the 'cancelled' status.",
    },
    {
        "slug": "appointment_rescheduled",
        "name": "Appointment Rescheduled",
        "trigger_type": "signal",
        "description": "Fires when a TokenBooking transitions to the 'rescheduled' status.",
    },
]


def seed_appointment_triggers(apps, schema_editor):
    NotificationTrigger = apps.get_model("care_im_wrapper", "NotificationTrigger")
    for trigger in TRIGGERS:
        NotificationTrigger.objects.get_or_create(
            slug=trigger["slug"],
            defaults={
                "name": trigger["name"],
                "trigger_type": trigger["trigger_type"],
                "description": trigger["description"],
            },
        )


def remove_appointment_triggers(apps, schema_editor):
    NotificationTrigger = apps.get_model("care_im_wrapper", "NotificationTrigger")
    NotificationTrigger.objects.filter(slug__in=[trigger["slug"] for trigger in TRIGGERS]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("care_im_wrapper", "0006_notificationevent_notificationrecipient_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_appointment_triggers, remove_appointment_triggers),
    ]
