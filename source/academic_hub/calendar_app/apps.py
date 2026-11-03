from django.apps import AppConfig


class CalendarAppConfig(AppConfig):
    # Not called "calendar": that would shadow Python's own calendar module,
    # which the view below depends on.
    default_auto_field = "django.db.models.BigAutoField"
    name = "calendar_app"
    verbose_name = "Calendar"
