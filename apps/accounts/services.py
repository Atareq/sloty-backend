from apps.accounts.models import User


def find_orphan_business_users():
    return User.objects.filter(
        is_active=True,
        profile__isnull=True,
    ).distinct()
