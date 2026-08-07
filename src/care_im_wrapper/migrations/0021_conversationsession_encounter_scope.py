from django.db import migrations, models


class Migration(migrations.Migration):
    """Encounter-scoped navigation: a second menu level, and the scope it carries.

    `menu_context` says which of the two menus AUTHENTICATED is showing. The encounter and
    prescription pairs carry the scope the sub-menu's fetchers read against, each with a
    pre-rendered label so redisplaying the sub-menu costs no extra query;
    `active_patient_label` does the same for the patient a staff member is viewing.

    Additive only, no data migration: existing sessions default to the main menu with no
    encounter open, which is exactly where a session that was mid-list should restart --
    the precedent 0020 set.
    """

    dependencies = [
        ("care_im_wrapper", "0020_conversationsession_data_offsets"),
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
                    ("selecting_encounter", "Selecting Encounter"),
                    ("selecting_prescription", "Selecting Prescription"),
                ],
                default="new",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="conversationsession",
            name="menu_context",
            field=models.CharField(
                choices=[("main", "Main"), ("encounter", "Encounter")], default="main", max_length=16
            ),
        ),
        migrations.AddField(
            model_name="conversationsession",
            name="active_patient_label",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="conversationsession",
            name="active_encounter_external_id",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="conversationsession",
            name="active_encounter_label",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="conversationsession",
            name="active_encounter_has_alternatives",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="conversationsession",
            name="active_prescription_external_id",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="conversationsession",
            name="active_prescription_label",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
