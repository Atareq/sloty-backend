from django.contrib import admin

from apps.clubs.models import Club, ClubMembership


@admin.register(Club)
class ClubAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "slug",
        "governorate",
        "city",
        "is_active",
    )
    search_fields = ("name", "slug", "governorate", "city", "phone_number")
    list_filter = ("is_active", "governorate", "city")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(ClubMembership)
class ClubMembershipAdmin(admin.ModelAdmin):
    list_display = (
        "club",
        "user",
        "role",
        "court",
        "manager_can_settle_transactions",
        "manager_can_change_pricing",
        "is_active",
        "created",
    )
    list_filter = ("role", "is_active")
    search_fields = ("club__name", "club__slug", "court__name", "user__username")
