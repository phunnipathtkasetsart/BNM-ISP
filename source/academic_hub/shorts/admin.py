"""Django admin pages for shorts and their comments."""

from typing import TYPE_CHECKING

from django.contrib import admin

from .models import Short, ShortComment

# ModelAdmin is generic only in the type stubs; at runtime it can't be
# subscripted, so the typed base classes exist for mypy alone.
if TYPE_CHECKING:
    ShortAdminBase = admin.ModelAdmin[Short]
    ShortCommentAdminBase = admin.ModelAdmin[ShortComment]
else:
    ShortAdminBase = ShortCommentAdminBase = admin.ModelAdmin


@admin.register(Short)
class ShortAdmin(ShortAdminBase):
    """Lets the Department see what has been uploaded."""

    list_display = (
        "title",
        "author_label",
        "duration_seconds",
        "size",
        "created_at",
    )
    search_fields = ("title", "description")
    readonly_fields = (
        "clip",
        "original_name",
        "size",
        "duration_seconds",
        "created_at",
        "updated_at",
    )
    filter_horizontal = ("tags",)


@admin.register(ShortComment)
class ShortCommentAdmin(ShortCommentAdminBase):
    """Lets the Department find and remove comments."""

    list_display = (
        "short",
        "author_label",
        "author_role_label",
        "parent",
        "created_at",
    )
    search_fields = ("body", "author_label")
    readonly_fields = (
        "short",
        "parent",
        "author",
        "author_label",
        "author_role_label",
        "created_at",
    )
