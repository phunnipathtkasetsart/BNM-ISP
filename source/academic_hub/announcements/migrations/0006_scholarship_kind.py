"""Give scholarship tags their own kind.

Scholarship and syllabus were both TOPIC, so the posting rules could not tell
them apart: the Department may tag a scholarship, but a syllabus is posted by
a lecturer inside a course later on. Any further scholarship tag gets this
kind and becomes available to the Department automatically.
"""

from django.db import migrations, models


def to_scholarship(apps, schema_editor):
    Tag = apps.get_model("announcements", "Tag")
    Tag.objects.filter(slug="scholarship").update(kind="scholarship")


def back_to_topic(apps, schema_editor):
    Tag = apps.get_model("announcements", "Tag")
    Tag.objects.filter(slug="scholarship").update(kind="topic")


class Migration(migrations.Migration):

    dependencies = [("announcements", "0005_programme_tags")]

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
                    ("scholarship", "Scholarship"),
                    ("topic", "Topic"),
                ],
                default="topic",
                max_length=20,
            ),
        ),
        migrations.RunPython(to_scholarship, back_to_topic),
    ]
