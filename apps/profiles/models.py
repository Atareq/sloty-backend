from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Profile(models.Model):
    """Application identity and the single runtime role authority."""

    class Role(models.TextChoices):
        OWNER = "OWNER", "Owner"
        STAFF = "STAFF", "Staff"
        ADMIN = "ADMIN", "Admin"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    role = models.CharField(max_length=16, choices=Role.choices, db_index=True)
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["role"])]

    def __str__(self) -> str:
        return f"{self.user} ({self.role})"


class OwnerProfile(models.Model):
    """Owner-specific extension; club ownership is intentionally many-to-many."""

    profile = models.OneToOneField(
        Profile,
        on_delete=models.CASCADE,
        related_name="owner_profile",
    )
    clubs = models.ManyToManyField("clubs.Club", related_name="owner_profiles")

    def clean(self):
        super().clean()
        if self.profile_id and self.profile.role != Profile.Role.OWNER:
            raise ValidationError(
                {"profile": "OwnerProfile requires an OWNER profile."}
            )

    def __str__(self) -> str:
        return f"OwnerProfile({self.profile.user})"


class StaffProfile(models.Model):
    """Staff-specific extension; a staff profile is assigned to one court."""

    profile = models.OneToOneField(
        Profile,
        on_delete=models.CASCADE,
        related_name="staff_profile",
    )
    court = models.ForeignKey(
        "courts.Court",
        on_delete=models.CASCADE,
        related_name="staff_profiles",
    )

    def clean(self):
        super().clean()
        if self.profile_id and self.profile.role != Profile.Role.STAFF:
            raise ValidationError({"profile": "StaffProfile requires a STAFF profile."})

    def __str__(self) -> str:
        return f"StaffProfile({self.profile.user}, court={self.court_id})"


class AdminProfile(models.Model):
    """Admin-specific extension. Platform-wide authority remains Profile.role."""

    profile = models.OneToOneField(
        Profile,
        on_delete=models.CASCADE,
        related_name="admin_profile",
    )

    def clean(self):
        super().clean()
        if self.profile_id and self.profile.role != Profile.Role.ADMIN:
            raise ValidationError(
                {"profile": "AdminProfile requires an ADMIN profile."}
            )

    def __str__(self) -> str:
        return f"AdminProfile({self.profile.user})"
