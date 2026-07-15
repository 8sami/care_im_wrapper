from django.db import migrations

# Slug of the TokenBooking context registered in NOTIFICATION_CONTEXT_REGISTRY
# (handlers/booking.py: BOOKING_CONTEXT_SLUG).
BOOKING_CONTEXT_SLUG = "token_booking"

APPOINTMENT_TRIGGER_SLUGS = [
    "appointment_confirmed",
    "appointment_cancelled",
    "appointment_rescheduled",
]


def set_context_slug(apps, schema_editor):
    NotificationTrigger = apps.get_model("care_im_wrapper", "NotificationTrigger")
    NotificationTrigger.objects.filter(slug__in=APPOINTMENT_TRIGGER_SLUGS).update(context_slug=BOOKING_CONTEXT_SLUG)


def unset_context_slug(apps, schema_editor):
    NotificationTrigger = apps.get_model("care_im_wrapper", "NotificationTrigger")
    NotificationTrigger.objects.filter(slug__in=APPOINTMENT_TRIGGER_SLUGS).update(context_slug="")


class Migration(migrations.Migration):
    dependencies = [
        ("care_im_wrapper", "0011_notificationtrigger_context_slug"),
    ]

    operations = [
        migrations.RunPython(set_context_slug, unset_context_slug),
    ]
