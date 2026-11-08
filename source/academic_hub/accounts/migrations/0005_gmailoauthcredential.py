from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_advisorassignment"),
    ]

    operations = [
        migrations.CreateModel(
            name="GmailOAuthCredential",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("google_email", models.EmailField(max_length=254)),
                ("encrypted_refresh_token", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="gmail_oauth_credential",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"db_table": "gmail_oauth_credentials"},
        ),
    ]
