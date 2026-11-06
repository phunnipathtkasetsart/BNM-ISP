from django.contrib import admin

from .models import ForumCategory, ForumQuestion


@admin.register(ForumCategory)
class ForumCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "created_by", "created_at")
    search_fields = ("name",)
    ordering = ("name",)


@admin.register(ForumQuestion)
class ForumQuestionAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "category",
        "visibility",
        "author_role",
        "is_pinned",
        "is_published",
        "created_at",
    )
    list_filter = ("visibility", "is_pinned", "is_published", "category")
    search_fields = ("title", "body", "author_label")
