from apps.profiles.models import Profile


def has_active_club_membership(user, club) -> bool:
    profile = getattr(user, "profile", None)
    if profile is None:
        return False
    if profile.role == Profile.Role.ADMIN:
        return True
    if profile.role == Profile.Role.OWNER:
        return profile.owner_profile.clubs.filter(pk=club.pk).exists()
    if profile.role == Profile.Role.STAFF:
        return profile.staff_profile.court.club_id == club.pk
    return False


def has_active_owner_membership(user, club) -> bool:
    profile = getattr(user, "profile", None)
    return bool(
        profile
        and profile.role == Profile.Role.OWNER
        and profile.owner_profile.clubs.filter(pk=club.pk).exists()
    )


def can_manage_club(user, club, action: str) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_platform_super_admin():
        return True
    return (
        has_active_owner_membership(user, club)
        if action in {"update", "partial_update"}
        else has_active_club_membership(user, club)
    )
