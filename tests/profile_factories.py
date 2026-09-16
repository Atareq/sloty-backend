"""Concrete inherited profile fixtures for domain behavior tests."""

from rest_framework.test import APIRequestFactory

from apps.accounts.models import User
from apps.common.authorization.resolver import resolve_club_scope
from apps.profiles.models import (
    AdminProfile,
    OwnerProfile,
    Profile,
    StaffCourtAccess,
    StaffProfile,
)

PASSWORD = "test-pass-123"


def create_user(username, **extra_fields):
    extra_fields.setdefault("password", PASSWORD)
    return User.objects.create_user(username=username, **extra_fields)


def grant_admin(user):
    profile, _ = AdminProfile.objects.get_or_create(
        user=user, defaults={"role": Profile.Role.ADMIN}
    )
    return profile


def grant_owner_club(user, club):
    owner, _ = OwnerProfile.objects.get_or_create(
        user=user, defaults={"role": Profile.Role.OWNER}
    )
    owner.clubs.add(club)
    return owner


def grant_staff_courts(user, club, courts=()):
    staff, _ = StaffProfile.objects.get_or_create(
        user=user, defaults={"role": Profile.Role.STAFF, "club": club}
    )
    if staff.club_id != club.pk:
        raise ValueError("A staff profile cannot belong to two clubs.")
    for court in courts:
        StaffCourtAccess.objects.get_or_create(staff_profile=staff, court=court)
    return staff


def grant_staff_court(user, court):
    return grant_staff_courts(user, court.club, (court,))


def create_admin_user(username="platform-admin", **extra_fields):
    user = create_user(username, **extra_fields)
    grant_admin(user)
    return user


def create_owner_user(username, clubs=(), **extra_fields):
    user = create_user(username, **extra_fields)
    owner = OwnerProfile.objects.create(user=user, role=Profile.Role.OWNER)
    owner.clubs.set(clubs)
    return user


def create_staff_user(username, court=None, club=None, courts=(), **extra_fields):
    user = create_user(username, **extra_fields)
    assignments = list(courts)
    if court is not None and court not in assignments:
        assignments.append(court)
    target_club = club or (assignments[0].club if assignments else None)
    grant_staff_courts(user, target_club, assignments)
    return user


def club_access(user, club):
    request = APIRequestFactory().get(f"/api/v1/clubs/{club.slug}/")
    request.user = user
    return resolve_club_scope(request, club.slug)


class ProfileIdentityMixin:
    def create_user(self, username, **extra_fields):
        return create_user(username, **extra_fields)

    def create_platform_admin(self, username="platform-admin", **extra_fields):
        return create_admin_user(username, **extra_fields)

    def club_access_for(self, user, club=None):
        return club_access(user, club or self.club)
