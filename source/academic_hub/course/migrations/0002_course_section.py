from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("course", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="course",
            name="section",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
    ]
