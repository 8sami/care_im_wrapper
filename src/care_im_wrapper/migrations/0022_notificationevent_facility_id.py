from django.db import migrations, models


def _tables(apps):
    return (
        apps.get_model("care_im_wrapper", "NotificationEvent")._meta.db_table,  # noqa: SLF001
        apps.get_model("emr", "FacilityOrganization")._meta.db_table,  # noqa: SLF001
    )


def backfill_facility_id(apps, schema_editor):
    """Derives facility_id from the root organization id the old cache held.

    One statement, not one per row: this runs against every historical event, and a
    row-at-a-time loop holds the migration's transaction open for the whole table.

    The cache stored exactly one root FacilityOrganization id, so its first element is the
    lookup. Events whose cache was empty had no resolvable facility and stay NULL, which
    authorization already treats as superuser-only.
    """
    event_table, org_table = _tables(apps)
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            f"""
            UPDATE {event_table} AS e
               SET facility_id = o.facility_id
              FROM {org_table} AS o
             WHERE o.id = e.facility_organization_cache[1]
               AND COALESCE(array_length(e.facility_organization_cache, 1), 0) > 0
            """  # noqa: S608
        )


def restore_facility_organization_cache(apps, schema_editor):
    """Rebuilds the root-org cache from facility_id, so the field can be dropped again."""
    event_table, org_table = _tables(apps)
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            f"""
            UPDATE {event_table} AS e
               SET facility_organization_cache = ARRAY[o.id]
              FROM {org_table} AS o
             WHERE o.facility_id = e.facility_id
               AND o.org_type = 'root'
               AND e.facility_id IS NOT NULL
            """  # noqa: S608
        )


class Migration(migrations.Migration):
    dependencies = [
        ("care_im_wrapper", "0021_conversationsession_encounter_scope"),
        ("emr", "0001_initial"),
    ]

    operations = [
        # Nullable, so Postgres adds it as metadata only -- no table rewrite.
        migrations.AddField(
            model_name="notificationevent",
            name="facility_id",
            field=models.IntegerField(blank=True, db_index=True, null=True),
        ),
        migrations.RunPython(backfill_facility_id, restore_facility_organization_cache),
    ]
