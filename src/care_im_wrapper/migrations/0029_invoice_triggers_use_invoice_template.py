from django.db import migrations

# `payment_status`'s body reads "Your payment of ... has been <status>", which describes a
# payment rather than the invoice these two triggers are about. `invoice_status` is the
# invoice-shaped template and takes the identical variables, so no handler change is needed.
OLD_TEMPLATE_SLUG = "payment_status"
NEW_TEMPLATE_SLUG = "invoice_status"
TRIGGER_SLUGS = ["invoice_issued", "invoice_cancelled"]


def use_invoice_template(apps, schema_editor):
    NotificationTrigger = apps.get_model("care_im_wrapper", "NotificationTrigger")
    NotificationTrigger.objects.filter(slug__in=TRIGGER_SLUGS).update(template_slug=NEW_TEMPLATE_SLUG)


def use_payment_template(apps, schema_editor):
    NotificationTrigger = apps.get_model("care_im_wrapper", "NotificationTrigger")
    NotificationTrigger.objects.filter(slug__in=TRIGGER_SLUGS).update(template_slug=OLD_TEMPLATE_SLUG)


class Migration(migrations.Migration):
    dependencies = [
        ("care_im_wrapper", "0028_seed_invoice_cancelled_trigger"),
    ]

    operations = [
        migrations.RunPython(use_invoice_template, use_payment_template),
    ]
