from apps.profiles.models import Profile


def has_active_club_profile_scope(profile, club) -> bool:
    if profile is None or not profile.is_enabled:
        return False
    if profile.role == Profile.Role.ADMIN:
        return True
    if profile.role == Profile.Role.OWNER:
        return profile.owner_profile.clubs.filter(pk=club.pk).exists()
    if profile.role == Profile.Role.STAFF:
        staff_profile = getattr(profile, "staff_profile", None)
        return bool(staff_profile and staff_profile.club_id == club.pk)
    return False


def has_active_owner_profile_scope(profile, club) -> bool:
    return bool(
        profile
        and profile.is_enabled
        and profile.role == Profile.Role.OWNER
        and profile.owner_profile.clubs.filter(pk=club.pk).exists()
    )


def can_manage_club(profile, club, action: str) -> bool:
    if profile is None or not profile.is_enabled:
        return False
    if profile.role == Profile.Role.ADMIN:
        return True
    return (
        has_active_owner_profile_scope(profile, club)
        if action in {"update", "partial_update"}
        else has_active_club_profile_scope(profile, club)
    )
