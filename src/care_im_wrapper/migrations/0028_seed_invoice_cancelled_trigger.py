from django.db import migrations

# Slug of the Invoice context registered in NOTIFICATION_CONTEXT_REGISTRY
# (handlers/billing.py: INVOICE_CONTEXT_SLUG), matching what invoice_issued uses.
INVOICE_CONTEXT_SLUG = "invoice"

# The same template invoice_issued renders; the handler supplies the `status` word.
TEMPLATE_SLUG = "payment_status"

TRIGGER = {
    "slug": "invoice_cancelled",
    "name": "Invoice Cancelled",
    "description": (
        "Fires when an Invoice the patient was already told about -- one in 'issued' or "
        "'balanced' -- moves to 'cancelled' or 'entered_in_error'. Both read as "
        "'cancelled' to the patient; a draft invoice never fires."
    ),
}


def seed_trigger(apps, schema_editor):
    NotificationTrigger = apps.get_model("care_im_wrapper", "NotificationTrigger")
    NotificationTrigger.objects.get_or_create(
        slug=TRIGGER["slug"],
        defaults={
            "name": TRIGGER["name"],
            "trigger_type": "signal",
            "description": TRIGGER["description"],
            "template_slug": TEMPLATE_SLUG,
            "context_slug": INVOICE_CONTEXT_SLUG,
        },
    )


def remove_trigger(apps, schema_editor):
    NotificationTrigger = apps.get_model("care_im_wrapper", "NotificationTrigger")
    NotificationTrigger.objects.filter(slug=TRIGGER["slug"]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("care_im_wrapper", "0027_seed_appointment_status_triggers"),
    ]

    operations = [
        migrations.RunPython(seed_trigger, remove_trigger),
    ]
