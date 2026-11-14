"""App configuration for Shorts."""

from django.apps import AppConfig


class ShortsConfig(AppConfig):
    """Registers the Shorts app and its signal handlers."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "shorts"
    verbose_name = "Shorts"

    def ready(self) -> None:
        # Importing the module is what registers the delete handler.
        # pylint: disable-next=import-outside-toplevel,unused-import
        from . import signals  # noqa: F401
