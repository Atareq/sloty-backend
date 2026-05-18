from django.contrib import admin

from apps.clubs.models import Club, ClubMembership


@admin.register(Club)
class ClubAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "city",
        "area",
        "is_active",
        "manager_can_settle_transactions",
        "manager_can_change_pricing",
    )
    search_fields = ("name", "city", "area", "phone_number")
    list_filter = ("is_active", "city")


@admin.register(ClubMembership)
class ClubMembershipAdmin(admin.ModelAdmin):
    list_display = ("club", "user", "role", "is_active", "created")
    list_filter = ("role", "is_active")
    search_fields = ("club__name", "user__username")
