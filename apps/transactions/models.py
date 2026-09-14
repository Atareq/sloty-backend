from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.bookings.models import Booking
from apps.clubs.models import Club
from apps.courts.models import Court


class Transaction(models.Model):
    class Type(models.TextChoices):
        PAYMENT = "PAYMENT", _("Payment")
        REFUND = "REFUND", _("Refund")

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
    )
    client_request_id = models.UUIDField(blank=True, null=True, db_index=True)
    transaction_type = models.CharField(
        max_length=20,
        choices=Type.choices,
        default=Type.PAYMENT,
        db_index=True,
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
    is_cancelled = models.BooleanField(default=False, db_index=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="cancelled_transactions",
    )
    cancelled_at = models.DateTimeField(blank=True, null=True, db_index=True)
    cancellation_reason = models.TextField(blank=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    created = models.DateTimeField(auto_now_add=True, db_index=True)
    modified = models.DateTimeField(auto_now=True)

    # Authorization Spine v2 resource-query contract
    # (apps/common/authorization/contracts.py). Operational boundary is
    # club+court. Staff created_by visibility is applied in
    # TransactionViewSet.filter_scoped_queryset() — it is not a Spine scope.
    authorization_config = {
        "scopes": {
            "club": {"path": "club"},
            "court": {"path": "court"},
        },
        "default_scope": "court",
        "select_related": (
            "club",
            "court",
            "booking",
            "booking__club_player__player_profile",
            "created_by",
            "cancelled_by",
        ),
        "prefetch_related": (),
    }

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=(
                    Q(transaction_type="PAYMENT", amount__gt=0)
                    | Q(transaction_type="REFUND", amount__lt=0)
                ),
                name="transaction_amount_matches_type",
            ),
            models.UniqueConstraint(
                fields=["club", "payment_reference"],
                condition=~Q(payment_reference=""),
                name="unique_non_blank_payment_reference_per_club",
            ),
            models.UniqueConstraint(
                fields=["club", "client_request_id"],
                condition=Q(client_request_id__isnull=False),
                name="unique_transaction_client_request_per_club",
            ),
        ]
        indexes = [
            models.Index(fields=["club", "created"]),
            models.Index(fields=["court", "created"]),
            models.Index(fields=["booking", "created"]),
            models.Index(fields=["created_by", "created"]),
            models.Index(fields=["club", "is_cancelled", "created"]),
            models.Index(fields=["booking", "is_cancelled"]),
            models.Index(fields=["booking", "transaction_type", "is_cancelled"]),
            models.Index(fields=["created_by", "is_cancelled", "created"]),
            models.Index(fields=["transaction_type", "created"]),
            models.Index(fields=["payment_method"]),
            models.Index(fields=["payment_reference"]),
            models.Index(fields=["club", "occurred_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.booking_id} - {self.amount} ({self.payment_method})"

    def clean(self):
        super().clean()
        errors = {}
        if self.amount is not None:
            if self.transaction_type == self.Type.PAYMENT and self.amount <= 0:
                errors["amount"] = "Payment amount must be greater than 0."
            if self.transaction_type == self.Type.REFUND and self.amount >= 0:
                errors["amount"] = "Refund amount must be less than 0."
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


class TransactionAttempt(models.Model):
    class Outcome(models.TextChoices):
        SUCCESS = "SUCCESS", _("Success")
        REJECTED = "REJECTED", _("Rejected")

    class Resolution(models.TextChoices):
        UNRESOLVED = "UNRESOLVED", _("Unresolved")
        DISMISSED = "DISMISSED", _("Dismissed")
        RESOLVED = "RESOLVED", _("Resolved")

    club = models.ForeignKey(
        Club,
        on_delete=models.CASCADE,
        related_name="transaction_attempts",
    )
    court = models.ForeignKey(
        Court,
        on_delete=models.CASCADE,
        related_name="transaction_attempts",
    )
    booking = models.ForeignKey(
        Booking,
        on_delete=models.CASCADE,
        related_name="transaction_attempts",
    )
    attempted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="transaction_attempts",
    )
    transaction = models.ForeignKey(
        Transaction,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="attempts",
    )
    client_request_id = models.UUIDField(blank=True, null=True, db_index=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(
        max_length=32,
        choices=Transaction.PaymentMethod.choices,
        db_index=True,
    )
    payment_reference = models.CharField(max_length=255, blank=True, default="")
    notes = models.TextField(blank=True)
    occurred_at = models.DateTimeField(db_index=True)
    outcome = models.CharField(max_length=32, choices=Outcome.choices, db_index=True)
    failure_code = models.CharField(max_length=128, blank=True)
    failure_details = models.JSONField(blank=True, default=dict)
    resolution = models.CharField(
        max_length=32,
        choices=Resolution.choices,
        default=Resolution.UNRESOLVED,
        db_index=True,
    )
    created = models.DateTimeField(auto_now_add=True, db_index=True)
    modified = models.DateTimeField(auto_now=True)

    # Authorization Spine v2 resource-query contract. Boundary is club+court.
    # Staff attempted_by restriction is applied in
    # TransactionAttemptViewSet.filter_scoped_queryset() — it is not a Spine scope.
    authorization_config = {
        "scopes": {
            "club": {"path": "club"},
            "court": {"path": "court"},
        },
        "default_scope": "court",
        "select_related": (
            "club",
            "court",
            "booking",
            "booking__club_player__player_profile",
            "attempted_by",
            "transaction",
        ),
        "prefetch_related": (),
    }

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=Q(amount__gt=0),
                name="transaction_attempt_amount_gt_zero",
            ),
            models.CheckConstraint(
                check=(
                    Q(
                        outcome="SUCCESS",
                        transaction__isnull=False,
                        failure_code="",
                    )
                    | (
                        Q(outcome="REJECTED", transaction__isnull=True)
                        & ~Q(failure_code="")
                    )
                ),
                name="transaction_attempt_outcome_consistent",
            ),
            models.CheckConstraint(
                check=~Q(
                    outcome="SUCCESS",
                    resolution="DISMISSED",
                ),
                name="transaction_attempt_success_not_dismissed",
            ),
            models.UniqueConstraint(
                fields=["club", "client_request_id"],
                condition=Q(client_request_id__isnull=False),
                name="transaction_attempt_client_request_once_per_club",
            ),
        ]
        indexes = [
            models.Index(fields=["club", "created"]),
            models.Index(fields=["club", "outcome", "created"]),
            models.Index(fields=["attempted_by", "created"]),
            models.Index(fields=["court", "created"]),
            models.Index(fields=["booking", "created"]),
            models.Index(fields=["club", "occurred_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.booking_id} payment attempt {self.client_request_id}"

    def clean(self):
        super().clean()
        errors = {}
        if self.amount is not None and self.amount <= 0:
            errors["amount"] = "Payment attempt amount must be greater than 0."
        if self.booking_id:
            if self.club_id and self.booking.club_id != self.club_id:
                errors["club"] = "Transaction attempt club must match the booking club."
            if self.court_id and self.booking.court_id != self.court_id:
                errors["court"] = (
                    "Transaction attempt court must match the booking court."
                )
        if self.transaction_id:
            if self.transaction.transaction_type != Transaction.Type.PAYMENT:
                errors["transaction"] = (
                    "Transaction attempt can only resolve to a payment transaction."
                )
            if self.booking_id and self.transaction.booking_id != self.booking_id:
                errors["transaction"] = (
                    "Transaction attempt transaction must match the booking."
                )
            if self.club_id and self.transaction.club_id != self.club_id:
                errors["transaction"] = (
                    "Transaction attempt transaction must belong to the same club."
                )
            if self.court_id and self.transaction.court_id != self.court_id:
                errors["transaction"] = (
                    "Transaction attempt transaction must belong to the same court."
                )
        if self.outcome == self.Outcome.SUCCESS:
            if self.transaction_id is None:
                errors["transaction"] = (
                    "Successful transaction attempts require a transaction."
                )
            if self.resolution == self.Resolution.DISMISSED:
                errors["resolution"] = (
                    "Successful transaction attempts cannot be dismissed."
                )
            if self.failure_code:
                errors["failure_code"] = (
                    "Successful transaction attempts must not have a failure code."
                )
        if self.outcome == self.Outcome.REJECTED:
            if self.transaction_id is not None:
                errors["transaction"] = (
                    "Rejected transaction attempts cannot link a transaction."
                )
            if not self.failure_code:
                errors["failure_code"] = (
                    "Rejected transaction attempts require a failure code."
                )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.payment_reference:
            self.payment_reference = self.payment_reference.strip()
        if self.booking_id:
            self.club_id = self.booking.club_id
            self.court_id = self.booking.court_id
        super().save(*args, **kwargs)
