from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from phonenumber_field.formfields import PhoneNumberField as PhoneNumberFormField

from apps.accounts.models import User


class UserAdminCreationForm(UserCreationForm):
    class Meta:
        model = User
        fields = ("phone_number", "email")
        field_classes = {"phone_number": PhoneNumberFormField}


class UserAdminChangeForm(UserChangeForm):
    class Meta:
        model = User
        fields = "__all__"
        field_classes = {"phone_number": PhoneNumberFormField}


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    form = UserAdminChangeForm
    add_form = UserAdminCreationForm
    ordering = ("phone_number",)
    list_display = (
        "phone_number",
        "email",
        "first_name",
        "last_name",
        "is_active",
        "is_staff",
    )
    search_fields = ("phone_number", "first_name", "last_name", "email")
    fieldsets = (
        (None, {"fields": ("phone_number", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name", "email")}),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("phone_number", "email", "password1", "password2"),
            },
        ),
    )
