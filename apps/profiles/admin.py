from django.contrib import admin

from apps.profiles.models import AdminProfile, OwnerProfile, Profile, StaffProfile


class OwnerProfileInline(admin.StackedInline):
    model = OwnerProfile
    extra = 0


class StaffProfileInline(admin.StackedInline):
    model = StaffProfile
    extra = 0


class AdminProfileInline(admin.StackedInline):
    model = AdminProfile
    extra = 0


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role")
    list_select_related = ("user",)
    inlines = (OwnerProfileInline, StaffProfileInline, AdminProfileInline)


@admin.register(OwnerProfile)
class OwnerProfileAdmin(admin.ModelAdmin):
    list_display = ("profile",)
    filter_horizontal = ("clubs",)


@admin.register(StaffProfile)
class StaffProfileAdmin(admin.ModelAdmin):
    list_display = ("profile", "court")
    list_select_related = ("profile__user", "court")


@admin.register(AdminProfile)
class AdminProfileAdmin(admin.ModelAdmin):
    list_display = ("profile",)
    list_select_related = ("profile__user",)
