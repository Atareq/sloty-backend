from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction

from apps.common.models import TimeStampedModel
from apps.courts.models import Court


class Profile(TimeStampedModel):
    """Concrete parent and sole application-role authority for an account."""

    class Role(models.TextChoices):
        OWNER = "OWNER", "Owner"
        STAFF = "STAFF", "Staff"
        ADMIN = "ADMIN", "Admin"
        PLAYER = "PLAYER", "Player"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    role = models.CharField(max_length=16, choices=Role.choices, db_index=True)
    is_enabled = models.BooleanField(default=True, db_index=True)

    def __str__(self):
        identity = self.user or "unlinked"
        return f"{identity} ({self.role})"

    def clean(self):
        super().clean()
        if self.role != self.Role.PLAYER and self.user_id is None:
            raise ValidationError({"user": f"{self.role} profiles require a user."})

        player_profile = apps.get_model("players", "PlayerProfile", require_ready=False)
        child_roles = (
            (OwnerProfile, self.Role.OWNER),
            (StaffProfile, self.Role.STAFF),
            (AdminProfile, self.Role.ADMIN),
            (player_profile, self.Role.PLAYER),
        )
        for child, role in child_roles:
            # Type checks validate the schema; authorization reads role only.
            if isinstance(self, child) or (
                self.pk and child.objects.filter(pk=self.pk).exists()
            ):
                if self.role != role:
                    raise ValidationError(
                        {"role": f"{child.__name__} requires role {role}."}
                    )
        if isinstance(self, StaffProfile) and self.pk and self.club_id:
            if self.court_accesses.exclude(court__club_id=self.club_id).exists():
                raise ValidationError(
                    {"club": "Assigned courts must belong to the staff member's club."}
                )

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if self.pk:
                Profile.objects.select_for_update().filter(pk=self.pk).first()
            self.full_clean()
            return super().save(*args, **kwargs)


class OwnerProfile(Profile):
    clubs = models.ManyToManyField("clubs.Club", related_name="owner_profiles")

    class Meta:
        default_related_name = "owner_profile"


class StaffProfile(Profile):
    club = models.ForeignKey(
        "clubs.Club", on_delete=models.CASCADE, related_name="staff_profiles"
    )

    authorization_config = {
        "scopes": {"club": {"path": "club"}},
        "default_scope": "club",
        "select_related": ("user", "club"),
        "prefetch_related": ("court_accesses__court",),
    }

    class Meta:
        default_related_name = "staff_profile"


class AdminProfile(Profile):
    class Meta:
        default_related_name = "admin_profile"


class StaffCourtAccess(TimeStampedModel):
    staff_profile = models.ForeignKey(
        StaffProfile, on_delete=models.CASCADE, related_name="court_accesses"
    )
    court = models.ForeignKey(
        "courts.Court", on_delete=models.CASCADE, related_name="staff_court_accesses"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["staff_profile", "court"], name="unique_staff_court_access"
            )
        ]

    def __str__(self):
        return f"StaffCourtAccess({self.staff_profile_id}, court={self.court_id})"

    def clean(self):
        super().clean()
        if self.staff_profile_id and self.court_id:
            if self.court.club_id != self.staff_profile.club_id:
                raise ValidationError(
                    {"court": "Assigned court must belong to the staff member's club."}
                )

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if self.staff_profile_id:
                self.staff_profile = StaffProfile.objects.select_for_update().get(
                    pk=self.staff_profile_id
                )
            if self.court_id:
                self.court = Court.objects.select_for_update().get(pk=self.court_id)
            self.full_clean()
            return super().save(*args, **kwargs)
