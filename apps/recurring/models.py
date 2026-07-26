from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from phonenumber_field.modelfields import PhoneNumberField

from apps.clubs.models import Club
from apps.courts.models import Court, CourtWorkingHour
from apps.transactions.models import Transaction


class RecurringAgreement(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", _("Active")
        CANCELLED = "CANCELLED", _("Cancelled")
        ACTION_REQUIRED = "ACTION_REQUIRED", _("Action required")

    class DepositStatus(models.TextChoices):
        HELD = "HELD", _("Held")
        REFUND_DUE = "REFUND_DUE", _("Refund due")
        REFUNDED = "REFUNDED", _("Refunded")
        FORFEITED = "FORFEITED", _("Forfeited")

    club = models.ForeignKey(
        Club,
        on_delete=models.CASCADE,
        related_name="recurring_agreements",
    )
    court = models.ForeignKey(
        Court,
        on_delete=models.CASCADE,
        related_name="recurring_agreements",
    )
    customer_name = models.CharField(max_length=255)
    customer_phone = PhoneNumberField()
    weekday = models.PositiveSmallIntegerField(choices=CourtWorkingHour.Weekday.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()
    start_date = models.DateField()
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    deposit_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    deposit_status = models.CharField(
        max_length=32,
        choices=DepositStatus.choices,
        default=DepositStatus.HELD,
        db_index=True,
    )
    refund_notice_days_snapshot = models.PositiveSmallIntegerField(
        validators=[MaxValueValidator(30)],
    )
    deposit_collected_at = models.DateTimeField()
    deposit_collected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="collected_recurring_deposits",
    )
    cancellation_requested_at = models.DateTimeField(blank=True, null=True)
    cancellation_effective_date = models.DateField(blank=True, null=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="cancelled_recurring_agreements",
    )
    cancellation_reason = models.TextField(blank=True)
    refund_due_at = models.DateTimeField(blank=True, null=True)
    refunded_at = models.DateTimeField(blank=True, null=True)
    refunded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="refunded_recurring_deposits",
    )
    action_required_code = models.CharField(max_length=128, blank=True)
    failed_occurrence_start = models.DateTimeField(blank=True, null=True)
    action_required_at = models.DateTimeField(blank=True, null=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="created_recurring_agreements",
    )
    created = models.DateTimeField(auto_now_add=True, db_index=True)
    modified = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["club", "status"]),
            models.Index(fields=["court", "status"]),
            models.Index(fields=["deposit_status"]),
            models.Index(fields=["start_date"]),
            models.Index(fields=["cancellation_effective_date"]),
            models.Index(fields=["created"]),
        ]

    def __str__(self) -> str:
        return f"{self.court} - {self.customer_name} ({self.get_weekday_display()})"

    def clean(self):
        super().clean()
        errors = {}
        if self.court_id and self.club_id and self.court.club_id != self.club_id:
            errors["club"] = "Agreement club must match the court club."
        if self.start_time and self.end_time and self.start_time >= self.end_time:
            errors["end_time"] = "end_time must be after start_time."
        if self.start_date is not None and self.weekday is not None:
            if self.start_date.weekday() != self.weekday:
                errors["start_date"] = "start_date must match the agreement weekday."
        if errors:
            raise ValidationError(errors)


class RecurringDepositTransaction(models.Model):
    class Type(models.TextChoices):
        COLLECTION = "COLLECTION", _("Deposit collection")
        REFUND = "REFUND", _("Deposit refund")

    club = models.ForeignKey(
        Club,
        on_delete=models.CASCADE,
        related_name="recurring_deposit_transactions",
    )
    court = models.ForeignKey(
        Court,
        on_delete=models.CASCADE,
        related_name="recurring_deposit_transactions",
    )
    agreement = models.ForeignKey(
        RecurringAgreement,
        on_delete=models.CASCADE,
        related_name="deposit_transactions",
    )
    transaction_type = models.CharField(
        max_length=32,
        choices=Type.choices,
        db_index=True,
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    payment_method = models.CharField(
        max_length=32,
        choices=Transaction.PaymentMethod.choices,
        db_index=True,
    )
    reference = models.CharField(max_length=255, blank=True, default="")
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="created_recurring_deposit_transactions",
    )
    created = models.DateTimeField(auto_now_add=True, db_index=True)
    modified = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=Q(amount__gt=0),
                name="recurring_deposit_transaction_amount_gt_zero",
            ),
            models.UniqueConstraint(
                fields=["club", "reference"],
                condition=~Q(reference=""),
                name="unique_non_blank_recurring_deposit_reference_per_club",
            ),
        ]
        indexes = [
            models.Index(fields=["club", "created"]),
            models.Index(fields=["court", "created"]),
            models.Index(fields=["agreement", "created"]),
            models.Index(fields=["transaction_type", "created"]),
            models.Index(fields=["created_by", "created"]),
            models.Index(fields=["reference"]),
        ]

    def __str__(self) -> str:
        return f"{self.transaction_type}:{self.agreement_id} - {self.amount}"

    def clean(self):
        super().clean()
        errors = {}
        if self.amount is not None and self.amount <= 0:
            errors["amount"] = "Amount must be greater than 0."
        if self.agreement_id:
            if self.club_id and self.agreement.club_id != self.club_id:
                errors["club"] = "Deposit transaction club must match the agreement."
            if self.court_id and self.agreement.court_id != self.court_id:
                errors["court"] = "Deposit transaction court must match the agreement."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.reference:
            self.reference = self.reference.strip()
        if self.agreement_id:
            self.club_id = self.agreement.club_id
            self.court_id = self.agreement.court_id
        super().save(*args, **kwargs)
