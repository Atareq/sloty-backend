"""Compatibility fixture for legacy, direct service tests.

Operational request handling uses ``RequestAccessContext`` from the
Authorization Spine.  This object intentionally has no persistence model and
must not be used by views or authorization resolution.
"""

from dataclasses import dataclass

from apps.profiles.models import Profile


@dataclass(frozen=True)
class ClubAccessContext:
    request: object
    club: object

    @property
    def user(self):
        return self.request.user

    @property
    def profile(self):
        return getattr(self.user, "profile", None)

    @property
    def is_platform_admin(self):
        return bool(self.profile and self.profile.role == Profile.Role.ADMIN)

    @property
    def is_owner(self):
        if not self.profile or self.profile.role != Profile.Role.OWNER:
            return False
        return self.profile.owner_profile.clubs.filter(pk=self.club.pk).exists()

    @property
    def is_staff(self):
        if not self.profile or self.profile.role != Profile.Role.STAFF:
            return False
        return self.profile.staff_profile.court.club_id == self.club.id

    def user_has_active_membership(self, user):
        try:
            profile = user.profile
        except Profile.DoesNotExist:
            return False
        if profile.role == Profile.Role.ADMIN:
            return True
        if profile.role == Profile.Role.OWNER:
            return profile.owner_profile.clubs.filter(pk=self.club.pk).exists()
        if profile.role == Profile.Role.STAFF:
            return profile.staff_profile.court.club_id == self.club.id
        return False

    def can_preview_settlement_for_user(self, user):
        return (
            self.is_platform_admin
            or self.is_owner
            or (self.is_staff and user == self.user)
        )

    def can_create_settlement(self, court=None):
        return self.is_platform_admin or self.is_owner

    def can_approve_settlement_for_user(self, user):
        return self.is_platform_admin or self.is_owner
