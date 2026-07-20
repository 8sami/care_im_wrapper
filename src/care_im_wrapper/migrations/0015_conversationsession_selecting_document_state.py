# Generated for the SELECTING_DOCUMENT conversation state (per-record document pick-list).

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("care_im_wrapper", "0014_documentlink"),
    ]

    operations = [
        migrations.AlterField(
            model_name="conversationsession",
            name="state",
            field=models.CharField(
                choices=[
                    ("new", "New"),
                    ("awaiting_yob", "Awaiting Year of Birth"),
                    ("ambiguous", "Ambiguous"),
                    ("authenticated", "Authenticated"),
                    ("cooldown", "Cooldown"),
                    ("awaiting_patient_search", "Awaiting Patient Search"),
                    ("selecting_patient", "Selecting Patient"),
                    ("selecting_document", "Selecting Document"),
                ],
                default="new",
                max_length=30,
            ),
        ),
    ]
