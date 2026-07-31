from django.db import migrations

# Slugs of the contexts registered in NOTIFICATION_CONTEXT_REGISTRY by each handler module
# (handlers/patient.py, handlers/billing.py, handlers/token.py, handlers/booking.py).
PATIENT_CONTEXT_SLUG = "patient"
INVOICE_CONTEXT_SLUG = "invoice"
TOKEN_CONTEXT_SLUG = "token"
APPOINTMENT_REMINDER_CONTEXT_SLUG = "appointment_reminder"

TRIGGERS = [
    {
        "slug": "patient_registered",
        "name": "Patient Registered",
        "description": "Fires when a Patient record is first created.",
        "template_slug": "patient_updates",
        "context_slug": PATIENT_CONTEXT_SLUG,
    },
    {
        "slug": "patient_discharged",
        "name": "Patient Discharged",
        "description": "Fires when an Encounter transitions to the 'discharged' status.",
        "template_slug": "patient_updates",
        "context_slug": PATIENT_CONTEXT_SLUG,
    },
    {
        "slug": "invoice_issued",
        "name": "Invoice Issued",
        "description": "Fires when an Invoice transitions to the 'issued' status.",
        "template_slug": "payment_status",
        "context_slug": INVOICE_CONTEXT_SLUG,
    },
    {
        "slug": "payment_recorded",
        "name": "Payment Recorded",
        "description": "Fires when a PaymentReconciliation reaches the 'complete' outcome.",
        "template_slug": "payment_status",
        "context_slug": INVOICE_CONTEXT_SLUG,
    },
    {
        "slug": "appointment_reminder",
        "name": "Appointment Reminder",
        "description": "Fires from the periodic sweep for bookings starting inside the reminder lead window.",
        "template_slug": "event_reminder",
        "context_slug": APPOINTMENT_REMINDER_CONTEXT_SLUG,
    },
    {
        "slug": "wait_time_update",
        "name": "Waiting Time Update",
        "description": "Fires when a queue Token is issued to a patient.",
        "template_slug": "wait_time_update",
        "context_slug": TOKEN_CONTEXT_SLUG,
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
                "template_slug": trigger["template_slug"],
                "context_slug": trigger["context_slug"],
            },
        )


def remove_triggers(apps, schema_editor):
    NotificationTrigger = apps.get_model("care_im_wrapper", "NotificationTrigger")
    NotificationTrigger.objects.filter(slug__in=[trigger["slug"] for trigger in TRIGGERS]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("care_im_wrapper", "0017_conversationsession_last_active_at"),
    ]

    operations = [
        migrations.RunPython(seed_triggers, remove_triggers),
    ]
