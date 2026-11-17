"""Concrete inherited profile fixtures for domain behavior tests."""

import itertools
import os

from rest_framework.test import APIRequestFactory

from apps.accounts.models import User
from apps.common.authorization.resolver import resolve_club_scope
from apps.players.models import PlayerProfile
from apps.profiles.models import (
    AdminProfile,
    OwnerProfile,
    Profile,
    StaffCourtAccess,
    StaffProfile,
)

PASSWORD = "test-pass-123"


def _phone_seq_start():
    worker = os.environ.get("PYTEST_XDIST_WORKER", "gw0")
    digits = "".join(ch for ch in worker if ch.isdigit()) or "0"
    # Seven digits follow +20109, so this stride fits up to 99 workers.
    return 1 + int(digits) * 100_000


_PHONE_SEQ = itertools.count(_phone_seq_start())


def next_test_phone():
    return f"+20109{next(_PHONE_SEQ):07d}"


def next_test_email(phone_number):
    digits = "".join(ch for ch in str(phone_number) if ch.isdigit())
    return f"user-{digits}@sloty.test"


def _is_phone_identifier(value):
    if value is None:
        return False
    text = str(value).strip()
    return bool(text) and (text[0] in "+0" or text.isdigit())


def create_user(phone_number=None, email=None, **extra_fields):
    extra_fields.setdefault("password", PASSWORD)
    if not _is_phone_identifier(phone_number):
        phone_number = next_test_phone()
    extra_fields.setdefault("email", email or next_test_email(phone_number))
    email = extra_fields.pop("email")
    return User.objects.create_user(
        phone_number=phone_number,
        email=email,
        **extra_fields,
    )


def create_player_profile(phone_number, full_name="", *, user=None):
    """Create an unlinked PlayerProfile unless an account is explicitly given."""
    return PlayerProfile.objects.create(
        user=user,
        role=Profile.Role.PLAYER,
        phone_number=phone_number,
        full_name=full_name,
    )


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


def create_admin_user(phone_number=None, **extra_fields):
    user = create_user(phone_number=phone_number, **extra_fields)
    grant_admin(user)
    return user


def create_owner_user(phone_number=None, clubs=(), **extra_fields):
    user = create_user(phone_number=phone_number, **extra_fields)
    owner = OwnerProfile.objects.create(user=user, role=Profile.Role.OWNER)
    owner.clubs.set(clubs)
    return user


def create_staff_user(
    phone_number=None, court=None, club=None, courts=(), **extra_fields
):
    user = create_user(phone_number=phone_number, **extra_fields)
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
    def create_user(self, phone_number=None, **extra_fields):
        return create_user(phone_number, **extra_fields)

    def create_platform_admin(self, phone_number=None, **extra_fields):
        return create_admin_user(phone_number, **extra_fields)

    def club_access_for(self, user, club=None):
        return club_access(user, club or self.club)
