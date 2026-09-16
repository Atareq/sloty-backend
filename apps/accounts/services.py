from django.db.models import Q

from apps.accounts.models import User


def find_orphan_business_users():
    return User.objects.filter(
        Q(profile__isnull=True) | Q(profile__is_enabled=False),
        is_active=True,
    ).distinct()
