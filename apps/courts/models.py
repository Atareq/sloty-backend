from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction

from apps.clubs.models import Club
from apps.common.models import SlotyTimeStampedModel
from apps.courts.pricing import is_valid_period_bounds


class Court(SlotyTimeStampedModel):
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
    is_active = models.BooleanField(default=True)
    requires_digital_payment_reference = models.BooleanField(default=False)
    minimum_deposit = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    internal_hold_expiry_hours = models.PositiveIntegerField(
        default=12,
        validators=[MinValueValidator(1)],
        help_text="OnHold period without payment",
    )
    cancellation_refund_notice_days = models.PositiveSmallIntegerField(
        blank=True,
        null=True,
        validators=[MaxValueValidator(30)],
        help_text=(
            "Null means the cancellation refund policy is not configured. "
            "0 means refundable until the affected occurrence starts. "
            "1-30 is the required calendar-day notice."
        ),
    )
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        "profiles.Profile",
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="created_courts",
    )

    # Authorization Spine v2 resource-query contract (apps/common/authorization/).
    # Court is the court-scope boundary entity itself, hence the reserved
    # "self" path. See apps/common/authorization/contracts.py for the
    # required shape (scopes / default_scope / select_related / prefetch_related).
    authorization_config = {
        "scopes": {
            "club": {"path": "club"},
            "court": {"path": "self"},
        },
        "default_scope": "court",
        "select_related": ("club",),
        "prefetch_related": (),
    }

    def __str__(self) -> str:
        return f"{self.club} - {self.name}"

    def clean(self):
        super().clean()
        if self.pk and self.club_id:
            if self.staff_court_accesses.exclude(
                staff_profile__club_id=self.club_id
            ).exists():
                raise ValidationError(
                    {"club": "Assigned staff must belong to the court's club."}
                )
        if self.pk and self.minimum_deposit is not None:
            # Lazy import avoids a models ↔ services import cycle at module load.
            from apps.courts.services import (
                lowest_working_hour_price_for_court,
                minimum_deposit_above_lowest_price_message,
            )

            lowest_price = lowest_working_hour_price_for_court(self)
            if lowest_price is not None and self.minimum_deposit > lowest_price:
                raise ValidationError(
                    {
                        "minimum_deposit": minimum_deposit_above_lowest_price_message(
                            lowest_price
                        )
                    }
                )

    def save(self, *args, **kwargs):
        if not self.pk:
            return super().save(*args, **kwargs)
        with transaction.atomic():
            Court.objects.select_for_update().filter(pk=self.pk).first()
            self.clean()
            return super().save(*args, **kwargs)


class CourtWorkingHour(models.Model):
    # Every court can have weekday-specific pricing periods.
    class Weekday(models.IntegerChoices):
        SATURDAY = 0, "Saturday"
        SUNDAY = 1, "Sunday"
        MONDAY = 2, "Monday"
        TUESDAY = 3, "Tuesday"
        WEDNESDAY = 4, "Wednesday"
        THURSDAY = 5, "Thursday"
        FRIDAY = 6, "Friday"

    court = models.ForeignKey(
        Court,
        on_delete=models.CASCADE,
        related_name="working_hours",
        db_index=False,
    )
    weekday = models.PositiveSmallIntegerField(choices=Weekday.choices)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["court", "weekday"],
                name="unique_court_weekday_working_hour",
            ),
        ]

    # Authorization Spine v2 resource-query contract. Club boundary is
    # reached through the owning Court; court scope is the direct FK.
    authorization_config = {
        "scopes": {
            "club": {"path": "court__club"},
            "court": {"path": "court"},
        },
        "default_scope": "court",
        "select_related": ("court", "court__club"),
        "prefetch_related": ("pricing_periods",),
    }

    def __str__(self) -> str:
        return f"{self.court} - {self.get_weekday_display()}"


class CourtWorkingHourPricePeriod(models.Model):
    working_hour = models.ForeignKey(
        CourtWorkingHour,
        on_delete=models.CASCADE,
        related_name="pricing_periods",
        db_index=False,
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
                check=~models.Q(starts_at=models.F("ends_at")),
                name="court_price_period_non_empty",
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
        if (
            self.starts_at
            and self.ends_at
            and not is_valid_period_bounds(self.starts_at, self.ends_at)
        ):
            errors["starts_at"] = "starts_at and ends_at must not be equal."
        if self.price is not None and self.price < Decimal("0.00"):
            errors["price"] = "Price must be greater than or equal to zero."
        if self.price is not None and self.working_hour_id is not None and not errors:
            court = self.working_hour.court
            sibling_prices = list(
                CourtWorkingHourPricePeriod.objects.filter(
                    working_hour__court_id=court.pk
                )
                .exclude(pk=self.pk)
                .values_list("price", flat=True)
            )
            sibling_prices.append(self.price)
            lowest_price = min(sibling_prices)
            if court.minimum_deposit > lowest_price:
                from apps.courts.services import (
                    minimum_deposit_above_lowest_price_message,
                )

                errors["price"] = minimum_deposit_above_lowest_price_message(
                    lowest_price
                )
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.working_hour} - {self.starts_at}-{self.ends_at}"
