from apps.accounts.models import User


def find_orphan_business_users():
    return (
        User.objects.filter(
            is_active=True,
            is_platform_admin=False,
        )
        .exclude(
            club_memberships__is_active=True,
        )
        .distinct()
    )
