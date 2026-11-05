from django.contrib import admin

from .models import Short


@admin.register(Short)
class ShortAdmin(admin.ModelAdmin):
    """Lets the Department check what has been uploaded before the feed exists."""

    list_display = ("title", "author_label", "duration_seconds", "size", "created_at")
    search_fields = ("title", "description")
    readonly_fields = ("clip", "original_name", "size", "duration_seconds", "created_at", "updated_at")
    filter_horizontal = ("tags",)
