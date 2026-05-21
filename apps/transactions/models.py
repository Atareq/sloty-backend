from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from apps.bookings.models import Booking
from apps.clubs.models import Club
from apps.courts.models import Court


class Transaction(models.Model):
    class PaymentMethod(models.TextChoices):
        CASH = "CASH", "Cash"
        DIGITAL_WALLET = "DIGITAL_WALLET", "Digital wallet"
        BANK_TRANSFER = "BANK_TRANSFER", "Bank transfer"
        OTHER = "OTHER", "Other"

    club = models.ForeignKey(
        Club,
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    court = models.ForeignKey(
        Court,
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    booking = models.ForeignKey(
        Booking,
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    payment_method = models.CharField(
        max_length=32,
        choices=PaymentMethod.choices,
        db_index=True,
    )
    payment_reference = models.CharField(max_length=255, blank=True, default="")
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="created_transactions",
    )
    created = models.DateTimeField(auto_now_add=True, db_index=True)
    modified = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=Q(amount__gt=0),
                name="transaction_amount_gt_zero",
            ),
            models.UniqueConstraint(
                fields=["club", "payment_reference"],
                condition=~Q(payment_reference=""),
                name="unique_non_blank_payment_reference_per_club",
            ),
        ]
        indexes = [
            models.Index(fields=["club", "created"]),
            models.Index(fields=["court", "created"]),
            models.Index(fields=["booking", "created"]),
            models.Index(fields=["created_by", "created"]),
            models.Index(fields=["payment_method"]),
            models.Index(fields=["payment_reference"]),
        ]

    def __str__(self) -> str:
        return f"{self.booking_id} - {self.amount} ({self.payment_method})"

    def clean(self):
        super().clean()
        errors = {}
        if self.amount is not None and self.amount <= 0:
            errors["amount"] = "Amount must be greater than 0."
        if self.booking_id:
            if self.club_id and self.booking.club_id != self.club_id:
                errors["club"] = "Transaction club must match the booking club."
            if self.court_id and self.booking.court_id != self.court_id:
                errors["court"] = "Transaction court must match the booking court."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.payment_reference:
            self.payment_reference = self.payment_reference.strip()
        if self.booking_id:
            self.club_id = self.booking.club_id
            self.court_id = self.booking.court_id
        super().save(*args, **kwargs)
