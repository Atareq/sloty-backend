import django_filters
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import User
from apps.common.search import customer_phone_search_q
from apps.profiles.models import Profile


class UserFilter(django_filters.FilterSet):
    search = django_filters.CharFilter(
        method="filter_search",
        help_text=_("Search users by username, name, email, or phone number."),
    )
    is_active = django_filters.BooleanFilter(
        field_name="is_active",
        help_text=_("Filter users by active status."),
    )
    role = django_filters.ChoiceFilter(
        choices=Profile.Role.choices,
        method="filter_role",
        help_text=_("Filter users by application profile role."),
    )
    club = django_filters.CharFilter(
        method="filter_club",
        help_text=_("Filter users by club ID or slug."),
    )

    class Meta:
        model = User
        fields = ("search", "is_active", "role", "club")

    def filter_search(self, queryset, name, value):
        cleaned = (value or "").strip()
        if not cleaned:
            return queryset
        return queryset.filter(
            Q(username__icontains=cleaned)
            | Q(first_name__icontains=cleaned)
            | Q(last_name__icontains=cleaned)
            | Q(email__icontains=cleaned)
            | Q(phone_number__icontains=cleaned)
            | customer_phone_search_q("phone_number", cleaned)
        )

    def filter_role(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(
            profile__role=value,
        ).distinct()

    def filter_club(self, queryset, name, value):
        cleaned = (value or "").strip()
        if not cleaned:
            return queryset
        if cleaned.isdigit():
            club_q = Q(profile__owner_profile__clubs__id=int(cleaned)) | Q(
                profile__staff_profile__court__club_id=int(cleaned)
            )
        else:
            club_q = Q(profile__owner_profile__clubs__slug=cleaned) | Q(
                profile__staff_profile__court__club__slug=cleaned
            )
        return queryset.filter(club_q).distinct()
