from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.text import slugify
from phonenumber_field.modelfields import PhoneNumberField

from apps.common.egypt_locations import (
    get_all_city_choices,
    get_governorate_choices,
    is_valid_city,
    is_valid_city_for_governorate,
    is_valid_governorate,
)


def generate_unique_club_slug(name: str, *, exclude_pk=None) -> str:
    base_slug = slugify(name) or "club"
    slug = base_slug
    suffix = 2
    queryset = Club.objects.all()
    if exclude_pk is not None:
        queryset = queryset.exclude(pk=exclude_pk)
    while queryset.filter(slug=slug).exists():
        slug = f"{base_slug}-{suffix}"
        suffix += 1
    return slug


class Club(models.Model):
    name = models.CharField(max_length=255, db_index=True)
    slug = models.SlugField(max_length=120, unique=True, db_index=True)
    governorate = models.CharField(
        max_length=64,
        choices=get_governorate_choices(),
        db_index=True,
    )
    city = models.CharField(
        max_length=120,
        choices=get_all_city_choices(),
        db_index=True,
    )
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

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = generate_unique_club_slug(self.name, exclude_pk=self.pk)
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        errors = {}
        if not is_valid_governorate(self.governorate):
            errors["governorate"] = "Invalid governorate choice."
        if not is_valid_city(self.city):
            errors["city"] = "Invalid city choice."
        elif self.governorate and not is_valid_city_for_governorate(
            self.governorate,
            self.city,
        ):
            errors["city"] = "City must belong to the selected governorate."
        if errors:
            raise ValidationError(errors)


class ClubMembership(models.Model):
    class Role(models.TextChoices):
        OWNER = "OWNER", "Owner"
        MANAGER = "MANAGER", "Manager"
        STAFF = "STAFF", "Staff"

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
    court = models.ForeignKey(
        "courts.Court",
        blank=True,
        null=True,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
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
                condition=Q(
                    is_active=True,
                    role__in=("OWNER", "MANAGER"),
                    court__isnull=True,
                ),
                name="unique_active_club_role_membership",
            ),
            models.UniqueConstraint(
                fields=["club", "user", "role", "court"],
                condition=Q(is_active=True, role="STAFF"),
                name="unique_active_staff_membership",
            ),
            models.UniqueConstraint(
                fields=["user", "role"],
                condition=Q(is_active=True, role="MANAGER"),
                name="unique_active_manager_club_membership",
            ),
            models.UniqueConstraint(
                fields=["user", "role"],
                condition=Q(is_active=True, role="STAFF"),
                name="unique_active_staff_club_membership",
            ),
        ]
        indexes = [
            models.Index(fields=["club", "role", "is_active"]),
            models.Index(fields=["court", "role", "is_active"]),
            models.Index(fields=["user", "role", "is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} - {self.club} ({self.role})"

    def clean(self):
        super().clean()
        errors = {}
        if self.role in {self.Role.OWNER, self.Role.MANAGER} and self.court_id:
            errors["court"] = "OWNER and MANAGER memberships cannot be court-scoped."
        if self.role == self.Role.STAFF:
            if not self.court_id:
                errors["court"] = "STAFF memberships require a court."
            elif self.club_id and self.court.club_id != self.club_id:
                errors["court"] = "Staff membership court must belong to the club."
        if errors:
            raise ValidationError(errors)
