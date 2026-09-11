"""Make SKE and CPE programme tags rather than department tags.

They scope an item to the students of one programme, which is a different
rule from a general department notice, so they need their own kind for
visible_to() to filter on.
"""

from django.db import migrations, models


def to_programme(apps, schema_editor):
    Tag = apps.get_model("announcements", "Tag")
    Tag.objects.filter(slug__in=["ske", "cpe"]).update(kind="programme")


def back_to_department(apps, schema_editor):
    Tag = apps.get_model("announcements", "Tag")
    Tag.objects.filter(slug__in=["ske", "cpe"]).update(kind="department")


class Migration(migrations.Migration):

    dependencies = [("announcements", "0004_add_major_tags")]

    operations = [
        migrations.AlterField(
            model_name="tag",
            name="kind",
            field=models.CharField(
                choices=[
                    ("department", "Department"),
                    ("course", "Course"),
                    ("lab", "Lab"),
                    ("programme", "Programme"),
                    ("topic", "Topic"),
                ],
                default="topic",
                max_length=20,
            ),
        ),
        migrations.RunPython(to_programme, back_to_department),
    ]
