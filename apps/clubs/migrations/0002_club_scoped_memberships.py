from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
from django.db.models import Q
from django.utils.text import slugify


def populate_club_slugs(apps, schema_editor):
    Club = apps.get_model("clubs", "Club")
    used_slugs = set()

    for club in Club.objects.order_by("id"):
        base_slug = slugify(club.name) or "club"
        slug = base_slug
        suffix = 2
        while slug in used_slugs or Club.objects.exclude(pk=club.pk).filter(slug=slug).exists():
            slug = f"{base_slug}-{suffix}"
            suffix += 1
        club.slug = slug
        club.save(update_fields=["slug"])
        used_slugs.add(slug)


def copy_court_staff_assignments(apps, schema_editor):
    ClubMembership = apps.get_model("clubs", "ClubMembership")
    CourtStaffAssignment = apps.get_model("courts", "CourtStaffAssignment")

    for assignment in CourtStaffAssignment.objects.select_related("court").order_by("id"):
        club_id = assignment.court.club_id
        duplicate = ClubMembership.objects.filter(
            club_id=club_id,
            user_id=assignment.user_id,
            role="STAFF",
            court_id=assignment.court_id,
            is_active=assignment.is_active,
        )
        if duplicate.exists():
            continue

        if assignment.is_active:
            active_duplicate = ClubMembership.objects.filter(
                user_id=assignment.user_id,
                role="STAFF",
                is_active=True,
            )
            if active_duplicate.exists():
                continue

        ClubMembership.objects.create(
            club_id=club_id,
            user_id=assignment.user_id,
            role="STAFF",
            court_id=assignment.court_id,
            is_active=assignment.is_active,
            created_by_id=assignment.created_by_id,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("clubs", "0001_initial"),
        ("courts", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="club",
            name="slug",
            field=models.SlugField(
                blank=True,
                db_index=True,
                max_length=120,
                null=True,
            ),
        ),
        migrations.RunPython(populate_club_slugs, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="club",
            name="slug",
            field=models.SlugField(db_index=True, max_length=120, unique=True),
        ),
        migrations.AddField(
            model_name="clubmembership",
            name="court",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="memberships",
                to="courts.court",
            ),
        ),
        migrations.AlterField(
            model_name="clubmembership",
            name="role",
            field=models.CharField(
                choices=[
                    ("OWNER", "Owner"),
                    ("MANAGER", "Manager"),
                    ("STAFF", "Staff"),
                ],
                max_length=16,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="clubmembership",
            name="unique_active_club_membership",
        ),
        migrations.RunPython(copy_court_staff_assignments, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name="clubmembership",
            index=models.Index(
                fields=["court", "role", "is_active"],
                name="clubs_clubm_court_i_8cb54c_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="clubmembership",
            constraint=models.UniqueConstraint(
                condition=Q(
                    ("court__isnull", True),
                    ("is_active", True),
                    ("role__in", ("OWNER", "MANAGER")),
                ),
                fields=("club", "user", "role"),
                name="unique_active_club_role_membership",
            ),
        ),
        migrations.AddConstraint(
            model_name="clubmembership",
            constraint=models.UniqueConstraint(
                condition=Q(("is_active", True), ("role", "STAFF")),
                fields=("club", "user", "role", "court"),
                name="unique_active_staff_membership",
            ),
        ),
        migrations.AddConstraint(
            model_name="clubmembership",
            constraint=models.UniqueConstraint(
                condition=Q(("is_active", True), ("role", "STAFF")),
                fields=("user", "role"),
                name="unique_active_staff_club_membership",
            ),
        ),
    ]
