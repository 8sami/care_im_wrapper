from django.db import migrations, models

# Restores the slugs the original 0007 seed migration used, undoing a stopgap rename.
STATUS_BY_SLUG = {
    "appointment_confirmed": "confirmed",
    "appointment_cancelled": "cancelled",
    "appointment_rescheduled": "rescheduled",
}


def set_template_slug_and_status(apps, schema_editor):
    NotificationTrigger = apps.get_model("care_im_wrapper", "NotificationTrigger")
    NotificationTrigger.objects.filter(slug="appointment_update").update(slug="appointment_confirmed")
    for slug, status in STATUS_BY_SLUG.items():
        NotificationTrigger.objects.filter(slug=slug).update(
            template_slug="appointment_update",
            default_variable_values={"status": status},
        )


def unset_template_slug_and_status(apps, schema_editor):
    NotificationTrigger = apps.get_model("care_im_wrapper", "NotificationTrigger")
    NotificationTrigger.objects.filter(slug="appointment_confirmed").update(slug="appointment_update")
    NotificationTrigger.objects.filter(slug__in=STATUS_BY_SLUG).update(template_slug="", default_variable_values=None)


class Migration(migrations.Migration):
    dependencies = [
        ("care_im_wrapper", "0008_notificationtemplate_parameter_format"),
    ]

    operations = [
        migrations.AddField(
            model_name="notificationtrigger",
            name="template_slug",
            field=models.CharField(default="", max_length=100),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="notificationtrigger",
            name="default_variable_values",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.RunPython(set_template_slug_and_status, unset_template_slug_and_status),
    ]
