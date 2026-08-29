from django.db import migrations

# The provider template was renamed from `document_ready_update` to `document_ready`.
# Only the template the trigger points at changes; the trigger's own slug is internal
# and stays, so nothing outside this table needs to know.
OLD_TEMPLATE_SLUG = "document_ready_update"
NEW_TEMPLATE_SLUG = "document_ready"
TRIGGER_SLUG = "document_ready_update"


def use_renamed_template(apps, schema_editor):
    NotificationTrigger = apps.get_model("care_im_wrapper", "NotificationTrigger")
    NotificationTrigger.objects.filter(slug=TRIGGER_SLUG).update(template_slug=NEW_TEMPLATE_SLUG)


def use_old_template(apps, schema_editor):
    NotificationTrigger = apps.get_model("care_im_wrapper", "NotificationTrigger")
    NotificationTrigger.objects.filter(slug=TRIGGER_SLUG).update(template_slug=OLD_TEMPLATE_SLUG)


class Migration(migrations.Migration):
    dependencies = [
        ("care_im_wrapper", "0030_alter_documentlink_object_kind"),
    ]

    operations = [
        migrations.RunPython(use_renamed_template, use_old_template),
    ]
