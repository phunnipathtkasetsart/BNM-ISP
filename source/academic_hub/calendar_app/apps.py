"""App configuration for the calendar."""

from django.apps import AppConfig


class CalendarAppConfig(AppConfig):
    """Registers the calendar app.

    Not called "calendar": that would shadow Python's own calendar module,
    which the views depend on.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "calendar_app"
    verbose_name = "Calendar"
