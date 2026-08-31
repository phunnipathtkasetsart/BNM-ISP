from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


class UserAdmin(BaseUserAdmin):
    ordering = ["nisit_id"]
    list_display = ["nisit_id", "first_name", "last_name", "email", "department", "is_staff"]
    search_fields = ["nisit_id", "first_name", "last_name", "email"]
    fieldsets = (
        (None, {"fields": ("nisit_id", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name", "email", "department")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("nisit_id", "email", "first_name", "last_name", "department", "password1", "password2"),
        }),
    )
    filter_horizontal = ("groups", "user_permissions")


admin.site.register(User, UserAdmin)
