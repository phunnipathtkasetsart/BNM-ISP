from django.apps import AppConfig


class ShortsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "shorts"
    verbose_name = "Shorts"

    def ready(self):
        from . import signals  # noqa: F401  (registers the delete handler)
