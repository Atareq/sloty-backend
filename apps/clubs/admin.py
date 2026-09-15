from django.contrib import admin

from apps.clubs.models import Club


@admin.register(Club)
class ClubAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "city", "is_active")
    search_fields = ("name", "slug")
