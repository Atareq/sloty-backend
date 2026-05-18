from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from apps.clubs.models import Club


class Court(models.Model):
    class SportType(models.TextChoices):
        FOOTBALL = "FOOTBALL", "Football"

    club = models.ForeignKey(
        Club,
        on_delete=models.CASCADE,
        related_name="courts",
    )
    name = models.CharField(max_length=255)
    sport_type = models.CharField(
        max_length=32,
        choices=SportType.choices,
        default=SportType.FOOTBALL,
        db_index=True,
    )
    default_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    slot_duration_minutes = models.PositiveIntegerField(
        default=60,
        validators=[MinValueValidator(1)],
    )
    is_active = models.BooleanField(default=True, db_index=True)
    requires_digital_payment_reference = models.BooleanField(default=False)
    internal_hold_expiry_hours = models.PositiveIntegerField(
        default=12,
        validators=[MinValueValidator(1)],
    )
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="created_courts",
    )
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["club"]),
            models.Index(fields=["is_active"]),
            models.Index(fields=["sport_type"]),
        ]

    def __str__(self) -> str:
        return f"{self.club} - {self.name}"


class CourtWorkingHour(models.Model):
    class Weekday(models.IntegerChoices):
        MONDAY = 0, "Monday"
        TUESDAY = 1, "Tuesday"
        WEDNESDAY = 2, "Wednesday"
        THURSDAY = 3, "Thursday"
        FRIDAY = 4, "Friday"
        SATURDAY = 5, "Saturday"
        SUNDAY = 6, "Sunday"

    court = models.ForeignKey(
        Court,
        on_delete=models.CASCADE,
        related_name="working_hours",
    )
    weekday = models.PositiveSmallIntegerField(choices=Weekday.choices)
    opens_at = models.TimeField(blank=True, null=True)
    closes_at = models.TimeField(blank=True, null=True)
    is_closed = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["court", "weekday"],
                name="unique_court_weekday_working_hour",
            ),
        ]
        indexes = [
            models.Index(fields=["court", "weekday"]),
        ]

    def clean(self):
        super().clean()
        if self.is_closed:
            return
        if self.opens_at is None or self.closes_at is None:
            raise ValidationError(
                "Open working hours require both opens_at and closes_at."
            )
        if self.opens_at >= self.closes_at:
            raise ValidationError("opens_at must be before closes_at.")

    def __str__(self) -> str:
        return f"{self.court} - {self.get_weekday_display()}"


class CourtStaffAssignment(models.Model):
    court = models.ForeignKey(
        Court,
        on_delete=models.CASCADE,
        related_name="staff_assignments",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="court_staff_assignments",
    )
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="created_court_staff_assignments",
    )
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["court", "user"],
                condition=Q(is_active=True),
                name="unique_active_court_staff_assignment",
            ),
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(is_active=True),
                name="unique_active_staff_court_assignment",
            ),
        ]
        indexes = [
            models.Index(fields=["court", "is_active"]),
            models.Index(fields=["user", "is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} - {self.court}"
