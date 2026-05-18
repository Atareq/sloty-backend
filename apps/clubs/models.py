from django.conf import settings
from django.db import models
from django.db.models import Q
from phonenumber_field.modelfields import PhoneNumberField


class Club(models.Model):
    name = models.CharField(max_length=255, db_index=True)
    city = models.CharField(max_length=120, db_index=True)
    area = models.CharField(max_length=120)
    address = models.TextField(blank=True)
    phone_number = PhoneNumberField(blank=True, null=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    manager_can_settle_transactions = models.BooleanField(default=False)
    manager_can_change_pricing = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="created_clubs",
    )
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["city"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self) -> str:
        return self.name


class ClubMembership(models.Model):
    class Role(models.TextChoices):
        OWNER = "OWNER", "Owner"
        MANAGER = "MANAGER", "Manager"

    club = models.ForeignKey(
        Club,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="club_memberships",
    )
    role = models.CharField(max_length=16, choices=Role.choices)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="created_club_memberships",
    )
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["club", "user", "role"],
                condition=Q(is_active=True),
                name="unique_active_club_membership",
            ),
            models.UniqueConstraint(
                fields=["user", "role"],
                condition=Q(is_active=True, role="MANAGER"),
                name="unique_active_manager_club_membership",
            ),
        ]
        indexes = [
            models.Index(fields=["club", "role", "is_active"]),
            models.Index(fields=["user", "role", "is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} - {self.club} ({self.role})"
