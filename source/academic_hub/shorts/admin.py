from django.contrib import admin

from .models import Short, ShortComment


@admin.register(Short)
class ShortAdmin(admin.ModelAdmin):
    """Lets the Department see what has been uploaded."""

    list_display = ("title", "author_label", "duration_seconds", "size", "created_at")
    search_fields = ("title", "description")
    readonly_fields = ("clip", "original_name", "size", "duration_seconds", "created_at", "updated_at")
    filter_horizontal = ("tags",)


@admin.register(ShortComment)
class ShortCommentAdmin(admin.ModelAdmin):
    list_display = ("short", "author_label", "author_role_label", "parent", "created_at")
    search_fields = ("body", "author_label")
    readonly_fields = ("short", "parent", "author", "author_label", "author_role_label", "created_at")
