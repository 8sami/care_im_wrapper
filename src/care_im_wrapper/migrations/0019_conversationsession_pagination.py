from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("care_im_wrapper", "0018_seed_remaining_notification_triggers"),
    ]

    operations = [
        migrations.AddField(
            model_name="conversationsession",
            name="data_menu_choice",
            field=models.CharField(blank=True, default="", max_length=8),
        ),
        migrations.AddField(
            model_name="conversationsession",
            name="data_page",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="conversationsession",
            name="search_query",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
