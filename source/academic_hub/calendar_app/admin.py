from django.contrib import admin

from .models import DayNote


@admin.register(DayNote)
class DayNoteAdmin(admin.ModelAdmin):
    list_display = ["date", "title", "user", "notify"]
    list_filter = ["notify"]
    search_fields = ["title", "user__nisit_id"]
