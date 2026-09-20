"""Behavioral and schema guarantees of the concrete Profile hierarchy."""

from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from rest_framework.test import APIRequestFactory

from apps.accounts.models import User
from apps.clubs.models import Club
from apps.common.authorization.querysets import scoped_queryset
from apps.common.authorization.resolver import resolve_club_scope, resolve_global_scope
from apps.common.exceptions import SlotyAPIException
from apps.courts.models import Court
from apps.players.models import PlayerProfile
from apps.profiles.models import (
    AdminProfile,
    OwnerProfile,
    Profile,
    StaffCourtAccess,
    StaffProfile,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def club():
    return Club.objects.create(
        name="Al Nasr", slug="al-nasr", governorate="ASSIUT", city="ASSIUT_MARKAZ"
    )


@pytest.fixture
def other_club():
    return Club.objects.create(
        name="Al Ahly", slug="al-ahly", governorate="ASSIUT", city="ASSIUT_MARKAZ"
    )


@pytest.fixture
def courts(club, other_club):
    return [
        Court.objects.create(club=c, name=name, default_price="200.00")
        for c, name in (
            (club, "Main"),
            (club, "Training"),
            (club, "Unassigned"),
            (other_club, "Away"),
        )
    ]


@pytest.fixture
def user():
    return User.objects.create_user(username="omar", password="test-pass-123")


def request_for(user):
    request = APIRequestFactory().get("/")
    request.user = user
    return request


@pytest.mark.parametrize(
    "child,role,kwargs",
    [
        (OwnerProfile, "OWNER", {}),
        (StaffProfile, "STAFF", {"club": "club"}),
        (AdminProfile, "ADMIN", {}),
        (PlayerProfile, "PLAYER", {"phone_number": "+201011112222"}),
    ],
)
def test_children_have_real_parent_link_and_shared_identity(
    child, role, kwargs, user, club
):
    create_kwargs = {
        key: (club if value == "club" else value) for key, value in kwargs.items()
    }
    instance = child.objects.create(
        user=user,
        role=role,
        **create_kwargs,
    )
    link = child._meta.parents[Profile]
    assert link.remote_field.parent_link and link.primary_key
    assert link.name == "profile_ptr"
    assert child.__bases__ == (Profile,)
    assert instance.pk == instance.profile_ptr_id == user.profile.pk
    assert Profile.objects.get(pk=instance.pk).user_id == user.pk
    assert "role" not in {field.name for field in child._meta.local_fields}
    assert instance.role == user.profile.role == role
    assert child.clean is Profile.clean
    assert child.save is Profile.save
    assert Profile.objects.filter(user=user).count() == 1


def test_profile_created_by_tracks_profile_creator(user, club):
    admin_user = User.objects.create_user(username="profile-creator")
    admin = AdminProfile.objects.create(user=admin_user, role=Profile.Role.ADMIN)
    owner = OwnerProfile.objects.create(
        user=user,
        role=Profile.Role.OWNER,
        created_by=admin,
    )

    assert owner.created_by_id == admin.pk
    assert list(admin.created_profiles.values_list("pk", flat=True)) == [owner.pk]
    assert "created_by" in {field.name for field in Profile._meta.fields}
    assert "created_by" not in {field.name for field in User._meta.fields}


@pytest.mark.parametrize(
    "child,role,kwargs",
    [
        (OwnerProfile, Profile.Role.OWNER, {}),
        (StaffProfile, Profile.Role.STAFF, {"club": "club"}),
        (AdminProfile, Profile.Role.ADMIN, {}),
    ],
)
def test_management_profiles_require_user(child, role, kwargs, club):
    create_kwargs = {
        key: (club if value == "club" else value) for key, value in kwargs.items()
    }
    with pytest.raises(ValidationError):
        child.objects.create(role=role, **create_kwargs)


def test_player_profile_user_is_optional_or_linked(user):
    anonymous_player = PlayerProfile.objects.create(
        role=Profile.Role.PLAYER,
        phone_number="+201011113333",
    )
    linked_player = PlayerProfile.objects.create(
        user=user,
        role=Profile.Role.PLAYER,
        phone_number="+201011114444",
    )
    assert anonymous_player.user_id is None
    assert linked_player.user_id == user.pk
    assert linked_player.user.profile.role == Profile.Role.PLAYER


@pytest.mark.parametrize("enabled", [True, False])
def test_user_cannot_have_second_profile_even_if_disabled(user, enabled):
    OwnerProfile.objects.create(user=user, role=Profile.Role.OWNER, is_enabled=enabled)
    with pytest.raises(ValidationError):
        AdminProfile.objects.create(
            user=user, role=Profile.Role.ADMIN, is_enabled=False
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        Profile.objects.bulk_create(
            [Profile(user=user, role=Profile.Role.STAFF, is_enabled=False)]
        )
    assert isinstance(Profile._meta.get_field("user"), models.OneToOneField)


@pytest.mark.parametrize(
    "child,kwargs",
    [
        (OwnerProfile, {}),
        (StaffProfile, {"club": "club"}),
        (AdminProfile, {}),
        (PlayerProfile, {"phone_number": "+201011115555"}),
    ],
)
def test_child_save_validates_inherited_role(child, kwargs, user, club):
    create_kwargs = {
        key: (club if value == "club" else value) for key, value in kwargs.items()
    }
    obj = child(
        user=user,
        role="INVALID",
        **create_kwargs,
    )
    with pytest.raises(ValidationError):
        obj.save()
    assert not Profile.objects.filter(user=user).exists()


@pytest.mark.parametrize(
    "child,role",
    [
        (OwnerProfile, "STAFF"),
        (StaffProfile, "ADMIN"),
        (AdminProfile, "OWNER"),
        (PlayerProfile, "OWNER"),
    ],
)
def test_child_type_cannot_contradict_stored_role(child, role, user, club):
    obj = child(
        user=user,
        role=role,
        **(
            {"club": club}
            if child is StaffProfile
            else {"phone_number": "+201011116666"} if child is PlayerProfile else {}
        ),
    )
    with pytest.raises(ValidationError, match="requires role"):
        obj.clean()


def test_parent_and_child_reject_role_change(user):
    owner = OwnerProfile.objects.create(user=user, role=Profile.Role.OWNER)
    for instance in (owner, Profile.objects.get(pk=owner.pk)):
        instance.role = Profile.Role.ADMIN
        with pytest.raises(ValidationError):
            instance.save()
    assert Profile.objects.get(pk=owner.pk).role == Profile.Role.OWNER


def test_shared_save_calls_validation_and_preserves_parent_values(user, club):
    staff = StaffProfile.objects.create(user=user, role=Profile.Role.STAFF, club=club)
    created = staff.created
    staff.is_enabled = False
    with patch.object(Profile, "clean", autospec=True, wraps=Profile.clean) as clean:
        staff.save(update_fields=["is_enabled"])
        clean.assert_called_once_with(staff)
    parent = Profile.objects.get(pk=staff.pk)
    assert not parent.is_enabled
    assert parent.created == created
    assert parent.role == Profile.Role.STAFF
    assert parent.user_id == user.pk


def test_owners_support_multiple_clubs_and_shared_ownership(user, club, other_club):
    owner = OwnerProfile.objects.create(user=user, role=Profile.Role.OWNER)
    owner.clubs.set([club, other_club])
    peer = OwnerProfile.objects.create(
        user=User.objects.create_user(username="peer"), role=Profile.Role.OWNER
    )
    peer.clubs.add(club)
    assert set(owner.clubs.all()) == {club, other_club}
    assert club.owner_profiles.count() == 2


def test_staff_has_one_club_and_multiple_unique_assignments(user, club, courts):
    staff = StaffProfile.objects.create(user=user, role=Profile.Role.STAFF, club=club)
    for court in courts[:2]:
        StaffCourtAccess.objects.create(staff_profile=staff, court=court)
    assert staff.club == club
    assert set(staff.court_accesses.values_list("court_id", flat=True)) == {
        c.pk for c in courts[:2]
    }
    with pytest.raises(ValidationError):
        StaffCourtAccess.objects.create(staff_profile=staff, court=courts[0])


def test_staff_requires_club(user):
    with pytest.raises(ValidationError):
        StaffProfile.objects.create(user=user, role=Profile.Role.STAFF)


def test_court_assignment_cannot_cross_clubs(user, club, courts):
    staff = StaffProfile.objects.create(user=user, role=Profile.Role.STAFF, club=club)
    assignment = StaffCourtAccess(staff_profile=staff, court=courts[3])
    with pytest.raises(ValidationError):
        assignment.clean()
    with pytest.raises(ValidationError):
        assignment.save()
    assert not staff.court_accesses.exists()


def test_staff_cannot_move_club_while_old_assignments_exist(
    user, club, other_club, courts
):
    staff = StaffProfile.objects.create(user=user, role=Profile.Role.STAFF, club=club)
    StaffCourtAccess.objects.create(staff_profile=staff, court=courts[0])
    staff.club = other_club
    with pytest.raises(ValidationError):
        staff.save()
    staff.refresh_from_db()
    assert staff.club == club


def test_admin_authority_reads_role_and_enabled_state(user, club):
    profile = Profile.objects.create(user=user, role=Profile.Role.ADMIN)
    assert resolve_global_scope(request_for(user)).role == Profile.Role.ADMIN
    assert resolve_club_scope(request_for(user), club.slug).role == Profile.Role.ADMIN
    profile.is_enabled = False
    profile.save()
    user.refresh_from_db()
    with pytest.raises(SlotyAPIException):
        resolve_club_scope(request_for(user), club.slug)


def test_django_admin_flags_do_not_grant_application_access(user, club):
    user.is_staff = user.is_superuser = True
    user.save()
    assert not hasattr(user, "role")
    assert not hasattr(user, "is_platform_super_admin")
    with pytest.raises(SlotyAPIException):
        resolve_club_scope(request_for(user), club.slug)


def test_staff_scope_uses_all_assigned_courts_and_ignores_token_claims(
    user, club, courts
):
    staff = StaffProfile.objects.create(user=user, role=Profile.Role.STAFF, club=club)
    for court in courts[:2]:
        StaffCourtAccess.objects.create(staff_profile=staff, court=court)
    request = request_for(user)
    request.auth = {"role": "ADMIN", "court_id": courts[3].pk}
    access = resolve_club_scope(request, club.slug)
    assert access.role == Profile.Role.STAFF
    assert set(scoped_queryset(access, Court).values_list("pk", flat=True)) == {
        c.pk for c in courts[:2]
    }
    staff.court_accesses.all().delete()
    access = resolve_club_scope(request_for(user), club.slug)
    assert access.court_ids == ()
    assert not scoped_queryset(access, Court).exists()


def test_disabled_profile_cannot_resolve_club_scope(user, club):
    owner = OwnerProfile.objects.create(
        user=user, role=Profile.Role.OWNER, is_enabled=False
    )
    owner.clubs.add(club)
    with pytest.raises(SlotyAPIException):
        resolve_club_scope(request_for(user), club.slug)


def test_owner_scope_is_owned_clubs_only(user, club, other_club):
    owner = OwnerProfile.objects.create(user=user, role=Profile.Role.OWNER)
    owner.clubs.add(club)
    assert resolve_club_scope(request_for(user), club.slug).owner_profile.pk == owner.pk
    with pytest.raises(SlotyAPIException):
        resolve_club_scope(request_for(user), other_club.slug)


def test_child_deletion_removes_parent_and_assignments_but_keeps_user(
    user, club, courts
):
    staff = StaffProfile.objects.create(user=user, role=Profile.Role.STAFF, club=club)
    StaffCourtAccess.objects.create(staff_profile=staff, court=courts[0])
    staff.delete()
    assert not Profile.objects.filter(user=user).exists()
    assert not StaffCourtAccess.objects.exists()
    assert User.objects.filter(pk=user.pk).exists()


def test_court_cannot_move_away_from_assigned_staff(user, club, other_club, courts):
    staff = StaffProfile.objects.create(user=user, role=Profile.Role.STAFF, club=club)
    StaffCourtAccess.objects.create(staff_profile=staff, court=courts[0])
    courts[0].club = other_club
    with pytest.raises(ValidationError):
        courts[0].save()
    courts[0].refresh_from_db()
    assert courts[0].club == club
