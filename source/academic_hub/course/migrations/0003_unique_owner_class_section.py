from django.db import migrations, models
from django.db.models.functions import Lower, Trim


def check_existing_duplicates(apps, schema_editor):
    Course = apps.get_model("course", "Course")
    duplicates = (Course.objects.using(schema_editor.connection.alias).order_by()
                  .annotate(n=Lower(Trim("name")), s=Lower(Trim("section")))
                  .values("owner_id", "n", "s").annotate(total=models.Count("pk"))
                  .filter(total__gt=1))
    if duplicates.exists():
        raise RuntimeError("Duplicate class names/sections exist for the same owner. Rename the duplicate classes before applying this migration; no classes have been changed or deleted.")


class Migration(migrations.Migration):
    dependencies = [("course", "0002_course_section")]
    operations = [
        migrations.RunPython(check_existing_duplicates, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="course",
            constraint=models.UniqueConstraint(
                models.F("owner"), Lower(Trim("name")), Lower(Trim("section")),
                name="unique_owner_class_section",
            ),
        ),
    ]
