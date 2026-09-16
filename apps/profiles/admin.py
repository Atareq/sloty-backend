from django.contrib import admin

from apps.profiles.models import (
    AdminProfile,
    OwnerProfile,
    Profile,
    StaffCourtAccess,
    StaffProfile,
)


class StaffCourtAccessInline(admin.TabularInline):
    model = StaffCourtAccess
    extra = 0


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "is_enabled")
    list_select_related = ("user",)


@admin.register(OwnerProfile)
class OwnerProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "is_enabled")
    list_select_related = ("user",)
    filter_horizontal = ("clubs",)


@admin.register(StaffProfile)
class StaffProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "club", "is_enabled")
    list_select_related = ("user", "club")
    inlines = (StaffCourtAccessInline,)


@admin.register(StaffCourtAccess)
class StaffCourtAccessAdmin(admin.ModelAdmin):
    list_display = ("staff_profile", "court")
    list_select_related = ("staff_profile__user", "court")


@admin.register(AdminProfile)
class AdminProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "is_enabled")
    list_select_related = ("user",)
