from django.db import migrations


class Migration(migrations.Migration):
    """Drops the column 0022 replaced, in its own migration.

    Kept apart from the backfill so the two can be deployed separately: the column can only
    go once no running code still reads it, and a deploy that stops between the two steps
    leaves a consistent database either way. Reversing this re-adds an empty column, which
    0022's own reverse then repopulates.
    """

    dependencies = [
        ("care_im_wrapper", "0023_remove_conversationsession_care_im_wra_phone_n_ca3042_idx"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="notificationevent",
            name="facility_organization_cache",
        ),
    ]
