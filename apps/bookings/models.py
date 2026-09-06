from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from phonenumber_field.modelfields import PhoneNumberField

from apps.clubs.models import Club
from apps.courts.models import Court


class Booking(models.Model):
    class Status(models.TextChoices):
        HOLD = "HOLD", _("Hold")
        CONFIRMED = "CONFIRMED", _("Confirmed")
        COMPLETED = "COMPLETED", _("Completed")
        CANCELLED = "CANCELLED", _("Cancelled")
        NO_SHOW = "NO_SHOW", _("No-show")
        EXPIRED = "EXPIRED", _("Expired")

    class Source(models.TextChoices):
        MANUAL = "MANUAL", _("Manual")
        ADMIN_CORRECTION = "ADMIN_CORRECTION", _("Admin correction")
        RECURRING = "RECURRING", _("Recurring")

    class RecurrenceStatus(models.TextChoices):
        ACTIVE = "ACTIVE", _("Active")
        RENEWED = "RENEWED", _("Renewed")
        ENDED = "ENDED", _("Ended")

    BLOCKING_STATUSES = (
        Status.HOLD,
        Status.CONFIRMED,
        Status.COMPLETED,
        Status.NO_SHOW,
    )
    LOCKED_STATUSES = (
        Status.COMPLETED,
        Status.CANCELLED,
        Status.NO_SHOW,
        Status.EXPIRED,
    )

    club = models.ForeignKey(
        Club,
        on_delete=models.CASCADE,
        related_name="bookings",
    )
    court = models.ForeignKey(
        Court,
        on_delete=models.CASCADE,
        related_name="bookings",
    )
    customer_name = models.CharField(max_length=255)
    customer_phone = PhoneNumberField()
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    total_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.HOLD,
        db_index=True,
    )
    source = models.CharField(
        max_length=32,
        choices=Source.choices,
        default=Source.MANUAL,
        db_index=True,
    )
    recurrence_status = models.CharField(
        max_length=32,
        choices=RecurrenceStatus.choices,
        blank=True,
        null=True,
        db_index=True,
    )
    previous_recurring_booking = models.OneToOneField(
        "self",
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="next_recurring_booking",
    )
    client_request_id = models.UUIDField(blank=True, null=True, db_index=True)
    notes = models.TextField(blank=True)
    cancellation_reason = models.TextField(blank=True)
    no_show_reason = models.TextField(blank=True)
    reschedule_reason = models.TextField(blank=True)
    completed_at = models.DateTimeField(blank=True, null=True, db_index=True)
    cancelled_at = models.DateTimeField(blank=True, null=True, db_index=True)
    no_show_at = models.DateTimeField(blank=True, null=True, db_index=True)
    expired_at = models.DateTimeField(blank=True, null=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="created_bookings",
    )
    created = models.DateTimeField(auto_now_add=True, db_index=True)
    modified = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=(~Q(source="RECURRING") | Q(recurrence_status__isnull=False)),
                name="booking_recurring_source_requires_status",
            ),
            models.CheckConstraint(
                check=(Q(source="RECURRING") | Q(recurrence_status__isnull=True)),
                name="booking_non_recurring_status_null",
            ),
            models.CheckConstraint(
                check=(
                    Q(source="RECURRING") | Q(previous_recurring_booking__isnull=True)
                ),
                name="booking_non_recurring_previous_null",
            ),
            models.UniqueConstraint(
                fields=["club", "client_request_id"],
                condition=Q(client_request_id__isnull=False),
                name="booking_client_request_once_per_club",
            ),
        ]
        indexes = [
            models.Index(fields=["court", "start_time"]),
            models.Index(fields=["club", "start_time"]),
            models.Index(fields=["status"]),
            models.Index(fields=["source"]),
            models.Index(
                fields=["court", "source", "recurrence_status"],
                name="bookings_bo_court_i_d3b92f_idx",
            ),
            models.Index(fields=["created_by"]),
            models.Index(fields=["created"]),
            models.Index(fields=["completed_at"]),
            models.Index(fields=["cancelled_at"]),
            models.Index(fields=["no_show_at"]),
            models.Index(fields=["expired_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.court} - {self.start_time:%Y-%m-%d %H:%M}"

    def clean(self):
        super().clean()
        errors = {}
        if self.start_time and self.end_time and self.start_time >= self.end_time:
            errors["end_time"] = "end_time must be after start_time."
        if self.court_id and self.club_id and self.court.club_id != self.club_id:
            errors["club"] = "Booking club must match the court club."
        if self.source != self.Source.RECURRING:
            if self.recurrence_status is not None:
                errors["recurrence_status"] = (
                    "Non-recurring bookings must not have recurrence status."
                )
            if self.previous_recurring_booking_id is not None:
                errors["previous_recurring_booking"] = (
                    "Non-recurring bookings must not have a previous recurring booking."
                )
        else:
            if self.recurrence_status is None:
                errors["recurrence_status"] = (
                    "Recurring bookings must have recurrence status."
                )
            if (
                self.recurrence_status == self.RecurrenceStatus.ACTIVE
                and self.status not in {self.Status.HOLD, self.Status.CONFIRMED}
            ):
                errors["recurrence_status"] = (
                    "Active recurrence is valid only for hold or confirmed bookings."
                )
            if (
                self.recurrence_status == self.RecurrenceStatus.RENEWED
                and self.status != self.Status.COMPLETED
            ):
                errors["recurrence_status"] = (
                    "Renewed recurrence is valid only for completed bookings."
                )
            if (
                self.previous_recurring_booking_id is not None
                and self.previous_recurring_booking
                and (
                    self.previous_recurring_booking.source != self.Source.RECURRING
                    or self.previous_recurring_booking.club_id != self.club_id
                    or self.previous_recurring_booking.court_id != self.court_id
                )
            ):
                errors["previous_recurring_booking"] = (
                    "Previous recurring booking must be "
                    "recurring in the same club and court."
                )
        if errors:
            raise ValidationError(errors)
