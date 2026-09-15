from django.db import migrations


def migrate_profiles(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    ClubMembership = apps.get_model("clubs", "ClubMembership")
    Profile = apps.get_model("profiles", "Profile")
    OwnerProfile = apps.get_model("profiles", "OwnerProfile")
    StaffProfile = apps.get_model("profiles", "StaffProfile")
    AdminProfile = apps.get_model("profiles", "AdminProfile")

    memberships = ClubMembership.objects.filter(is_active=True, deleted_at__isnull=True)
    for user in User.objects.all().iterator():
        roles = set(memberships.filter(user_id=user.pk).values_list("role", flat=True))
        non_manager_roles = roles - {"MANAGER"}
        if len(non_manager_roles) > 1:
            raise RuntimeError(
                f"User {user.pk} has incompatible active roles {sorted(non_manager_roles)}; "
                "resolve this data before the Profile migration."
            )
        if user.is_platform_admin:
            if non_manager_roles:
                raise RuntimeError(
                    f"User {user.pk} is both platform admin and {sorted(non_manager_roles)}; "
                    "resolve this data before the Profile migration."
                )
            profile, _ = Profile.objects.get_or_create(user_id=user.pk, defaults={"role": "ADMIN"})
            AdminProfile.objects.get_or_create(profile_id=profile.pk)
            continue
        if non_manager_roles == {"OWNER"}:
            profile, _ = Profile.objects.get_or_create(user_id=user.pk, defaults={"role": "OWNER"})
            owner_profile, _ = OwnerProfile.objects.get_or_create(profile_id=profile.pk)
            owner_profile.clubs.add(*memberships.filter(user_id=user.pk, role="OWNER").values_list("club_id", flat=True))
            continue
        if non_manager_roles == {"STAFF"}:
            membership = memberships.filter(user_id=user.pk, role="STAFF").get()
            profile, _ = Profile.objects.get_or_create(user_id=user.pk, defaults={"role": "STAFF"})
            StaffProfile.objects.get_or_create(profile_id=profile.pk, defaults={"court_id": membership.court_id})
            continue
        if roles == {"MANAGER"}:
            # Managers are retired without privilege conversion. Retain the
            # historical account/audit identity but revoke login access.
            User.objects.filter(pk=user.pk).update(is_active=False)


class Migration(migrations.Migration):
    dependencies = [
        ("profiles", "0001_initial"),
    ]

    operations = [migrations.RunPython(migrate_profiles, migrations.RunPython.noop)]
