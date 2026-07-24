from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.clubs.models import Club


class Court(models.Model):
    class SportType(models.TextChoices):
        FOOTBALL = "FOOTBALL", "Football"
        PADEL = "PADEL", "Padel"
        TENNIS = "TENNIS", "Tennis"

    club = models.ForeignKey(
        Club,
        on_delete=models.CASCADE,
        related_name="courts",
    )
    players_count = models.PositiveIntegerField(
        default=10,
        validators=[
            MinValueValidator(1),
            MaxValueValidator(100),
        ],
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
        help_text="OnHold period without payment",
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
    # Every court can have a daily different working hours
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


class CourtWorkingHourPricePeriod(models.Model):
    working_hour = models.ForeignKey(
        CourtWorkingHour,
        on_delete=models.CASCADE,
        related_name="pricing_periods",
    )
    starts_at = models.TimeField()
    ends_at = models.TimeField()
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )

    class Meta:
        ordering = ("starts_at", "id")
        constraints = [
            models.CheckConstraint(
                check=models.Q(starts_at__lt=models.F("ends_at")),
                name="court_price_period_starts_before_ends",
            ),
            models.CheckConstraint(
                check=models.Q(price__gte=Decimal("0.00")),
                name="court_price_period_price_non_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["working_hour", "starts_at"]),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.starts_at and self.ends_at and self.starts_at >= self.ends_at:
            errors["starts_at"] = "starts_at must be before ends_at."
        if self.price is not None and self.price < Decimal("0.00"):
            errors["price"] = "Price must be greater than or equal to zero."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.working_hour} - {self.starts_at}-{self.ends_at}"
