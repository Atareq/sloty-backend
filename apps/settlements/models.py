from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from apps.clubs.models import Club
from apps.courts.models import Court
from apps.transactions.models import Transaction


class Settlement(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        SETTLED = "SETTLED", "Settled"

    club = models.ForeignKey(
        Club,
        on_delete=models.CASCADE,
        related_name="settlements",
    )
    court = models.ForeignKey(
        Court,
        blank=True,
        null=True,
        on_delete=models.CASCADE,
        related_name="settlements",
    )
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    total_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    transaction_count = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="created_settlements",
    )
    settled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="settled_settlements",
    )
    settled_at = models.DateTimeField(blank=True, null=True, db_index=True)
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=Q(period_start__lt=models.F("period_end")),
                name="settlement_period_start_before_end",
            ),
            models.CheckConstraint(
                check=Q(total_amount__gte=0),
                name="settlement_total_amount_gte_zero",
            ),
            models.CheckConstraint(
                check=Q(transaction_count__gte=0),
                name="settlement_transaction_count_gte_zero",
            ),
        ]
        indexes = [
            models.Index(fields=["club", "created"]),
            models.Index(fields=["club", "status"]),
            models.Index(fields=["club", "period_start", "period_end"]),
            models.Index(fields=["court", "period_start", "period_end"]),
            models.Index(fields=["settled_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.club} settlement {self.id or 'new'}"

    def clean(self):
        super().clean()
        errors = {}
        if (
            self.period_start
            and self.period_end
            and self.period_start >= self.period_end
        ):
            errors["period_end"] = "period_end must be after period_start."
        if self.court_id and self.club_id and self.court.club_id != self.club_id:
            errors["court"] = "Settlement court must belong to the selected club."
        if errors:
            raise ValidationError(errors)


class SettlementTransaction(models.Model):
    settlement = models.ForeignKey(
        Settlement,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    transaction = models.OneToOneField(
        Transaction,
        on_delete=models.CASCADE,
        related_name="settlement_line",
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=Q(amount__gt=0),
                name="settlement_transaction_amount_gt_zero",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.settlement_id} - {self.transaction_id}"

    def clean(self):
        super().clean()
        if self.amount is not None and self.amount <= 0:
            raise ValidationError({"amount": "Amount must be greater than 0."})
