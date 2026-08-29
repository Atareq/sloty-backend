from apps.accounts.models import User
from apps.clubs.models import ClubMembership


def find_orphan_business_users():
    return (
        User.objects.filter(
            is_active=True,
            is_platform_admin=False,
        )
        .exclude(
            club_memberships__in=ClubMembership.objects.granting_access(),
        )
        .distinct()
    )
