from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.clubs.models import Club
from apps.courts.models import Court


class AuditLog(models.Model):
    class Action(models.TextChoices):
        BOOKING_CREATED = "BOOKING_CREATED", _("Booking created")
        BOOKING_UPDATED = "BOOKING_UPDATED", _("Booking updated")
        BOOKING_CANCELLED = "BOOKING_CANCELLED", _("Booking cancelled")
        BOOKING_COMPLETED = "BOOKING_COMPLETED", _("Booking completed")
        BOOKING_NO_SHOW = "BOOKING_NO_SHOW", _("Booking no-show")
        BOOKING_EXPIRED = "BOOKING_EXPIRED", _("Booking expired")
        BOOKING_RESCHEDULED = "BOOKING_RESCHEDULED", _("Booking rescheduled")
        TRANSACTION_CREATED = "TRANSACTION_CREATED", _("Transaction created")
        TRANSACTION_CANCELLED = "TRANSACTION_CANCELLED", _("Transaction cancelled")
        SETTLEMENT_CREATED = "SETTLEMENT_CREATED", _("Settlement created")
        SETTLEMENT_MARKED_SETTLED = (
            "SETTLEMENT_MARKED_SETTLED",
            _("Settlement marked settled"),
        )
        MEMBERSHIP_DELETED = "MEMBERSHIP_DELETED", _("Membership deleted")

    club = models.ForeignKey(
        Club,
        on_delete=models.CASCADE,
        related_name="audit_logs",
    )
    court = models.ForeignKey(
        Court,
        blank=True,
        null=True,
        on_delete=models.CASCADE,
        related_name="audit_logs",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=64, choices=Action.choices, db_index=True)
    entity_type = models.CharField(max_length=64, db_index=True)
    entity_id = models.PositiveIntegerField(db_index=True)
    before_data = models.JSONField(blank=True, default=dict)
    after_data = models.JSONField(blank=True, default=dict)
    metadata = models.JSONField(blank=True, default=dict)
    created = models.DateTimeField(auto_now_add=True, db_index=True)

    # Authorization Spine v2 resource-query contract
    # (apps/common/authorization/contracts.py). Audit is a club-scoped read
    # model: every event belongs to one club. Court is an optional event
    # attribute (nullable for membership deletes and some settlements), not
    # a security boundary. Do not apply Booking/Transaction court scope or
    # Settlement collector scope to AuditLog rows.
    authorization_config = {
        "scopes": {
            "club": {"path": "club"},
        },
        "default_scope": "club",
        "select_related": ("club", "court", "actor"),
        "prefetch_related": (),
    }

    class Meta:
        ordering = ("-created", "-id")
        indexes = [
            models.Index(fields=["club", "created"]),
            models.Index(fields=["club", "action"]),
            models.Index(fields=["club", "entity_type", "entity_id"]),
            models.Index(fields=["club", "actor", "created"]),
            models.Index(fields=["club", "court", "created"]),
        ]

    def __str__(self) -> str:
        return f"{self.club} {self.action} {self.entity_type}:{self.entity_id}"
