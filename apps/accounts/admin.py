from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from apps.accounts.models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = (
        "username",
        "email",
        "is_platform_admin",
        "phone_number",
        "is_active",
        "is_staff",
        "is_superuser",
    )
    fieldsets = DjangoUserAdmin.fieldsets + (
        (
            "Sloty profile",
            {
                "fields": (
                    "is_platform_admin",
                    "phone_number",
                    "created_by",
                )
            },
        ),
    )
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        (
            "Sloty profile",
            {
                "fields": (
                    "is_platform_admin",
                    "phone_number",
                    "created_by",
                )
            },
        ),
    )
