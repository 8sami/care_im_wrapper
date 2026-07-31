from django.db import migrations, models


class Migration(migrations.Migration):
    """Paging state moves from a page counter to a stack of absolute record offsets.

    A page trimmed to fit a character budget has no fixed size, so `page * page_size` no
    longer says where the next page starts -- and advancing by the full page size after
    trimming would skip the records that were trimmed off, making them unreachable.

    No data migration: the stack starts empty, which is the first page. A session mid-list
    at deploy time simply restarts that list from the top on its next turn.
    """

    dependencies = [
        ("care_im_wrapper", "0019_conversationsession_pagination"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="conversationsession",
            name="data_page",
        ),
        migrations.AddField(
            model_name="conversationsession",
            name="data_offsets",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="conversationsession",
            name="data_shown",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
