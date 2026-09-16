from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.clubs.models import Club
from apps.courts.models import Court
from apps.transactions.models import Transaction


class Settlement(models.Model):
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
    total_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )
    notes = models.TextField(blank=True)
    settled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="settled_settlements",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    authorization_config = {
        "scopes": {
            "club": {"path": "club"},
        },
        "default_scope": "club",
        "select_related": (
            "club",
            "court",
            "settled_by",
        ),
        "prefetch_related": (),
    }

    class Meta:
        indexes = [
            models.Index(fields=["club", "created_at"]),
            models.Index(fields=["settled_by"]),
        ]

    def __str__(self) -> str:
        return f"{self.club} settlement {self.id or 'new'}"

    @property
    def transaction_count(self) -> int:
        if hasattr(self, "annotated_transaction_count"):
            return self.annotated_transaction_count
        return self.lines.count()

    @property
    def period_start(self):
        if hasattr(self, "annotated_period_start"):
            return self.annotated_period_start
        return (
            self.lines.order_by("transaction__created")
            .values_list("transaction__created", flat=True)
            .first()
        )

    @property
    def period_end(self):
        if hasattr(self, "annotated_period_end"):
            return self.annotated_period_end
        return (
            self.lines.order_by("-transaction__created")
            .values_list("transaction__created", flat=True)
            .first()
        )

    def clean(self):
        super().clean()
        if self.court_id and self.club_id and self.court.club_id != self.club_id:
            raise ValidationError(
                {"court": "Settlement court must belong to the selected club."}
            )


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
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=~Q(amount=0),
                name="settlement_transaction_amount_non_zero",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.settlement_id} - {self.transaction_id}"

    def clean(self):
        super().clean()
        if self.amount is not None and self.amount == 0:
            raise ValidationError({"amount": "Amount must not be zero."})
