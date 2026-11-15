"""Django admin page for day notes."""

from typing import TYPE_CHECKING

from django.contrib import admin

from .models import DayNote

# ModelAdmin is generic only in the type stubs; at runtime it can't be
# subscripted, so the typed base class exists for mypy alone.
if TYPE_CHECKING:
    DayNoteAdminBase = admin.ModelAdmin[DayNote]
else:
    DayNoteAdminBase = admin.ModelAdmin


@admin.register(DayNote)
class DayNoteAdmin(DayNoteAdminBase):
    """Lets the Department look up a note by title or student ID."""

    list_display = ["date", "title", "user", "notify"]
    list_filter = ["notify"]
    search_fields = ["title", "user__nisit_id"]
