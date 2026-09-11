"""Add the two programme tags, SKE and CPE.

Kept out of the seed command alone so a database that is never seeded still
gets them: they are part of the taxonomy, not demo content.
"""

from django.db import migrations

MAJORS = [("ske", "SKE"), ("cpe", "CPE")]


def add_major_tags(apps, schema_editor):
    Tag = apps.get_model("announcements", "Tag")
    for slug, label in MAJORS:
        Tag.objects.get_or_create(
            slug=slug, defaults={"label": label, "kind": "department"}
        )


def drop_major_tags(apps, schema_editor):
    Tag = apps.get_model("announcements", "Tag")
    Tag.objects.filter(slug__in=[s for s, _ in MAJORS]).delete()


class Migration(migrations.Migration):

    dependencies = [("announcements", "0003_merge_lab_tags")]

    operations = [migrations.RunPython(add_major_tags, drop_major_tags)]
