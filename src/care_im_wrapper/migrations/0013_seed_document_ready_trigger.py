from django.db import migrations

# Slug of the DiagnosticReportContext registered in NOTIFICATION_CONTEXT_REGISTRY
# (handlers/diagnostic_report.py: DOCUMENT_READY_CONTEXT_SLUG).
DOCUMENT_READY_CONTEXT_SLUG = "diagnostic_report"
DOCUMENT_READY_TRIGGER_SLUG = "document_ready_update"


def seed_document_ready_trigger(apps, schema_editor):
    NotificationTrigger = apps.get_model("care_im_wrapper", "NotificationTrigger")
    NotificationTrigger.objects.get_or_create(
        slug=DOCUMENT_READY_TRIGGER_SLUG,
        defaults={
            "name": "Document Ready",
            "trigger_type": "signal",
            "description": "Fires when a DiagnosticReport transitions to the 'final' status.",
            "template_slug": DOCUMENT_READY_TRIGGER_SLUG,
            "context_slug": DOCUMENT_READY_CONTEXT_SLUG,
        },
    )


def remove_document_ready_trigger(apps, schema_editor):
    NotificationTrigger = apps.get_model("care_im_wrapper", "NotificationTrigger")
    NotificationTrigger.objects.filter(slug=DOCUMENT_READY_TRIGGER_SLUG).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("care_im_wrapper", "0012_seed_trigger_context_slug"),
    ]

    operations = [
        migrations.RunPython(seed_document_ready_trigger, remove_document_ready_trigger),
    ]
