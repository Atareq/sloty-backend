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
from apps.players.models import ClubPlayer


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

    class LastStatusActorType(models.TextChoices):
        INTERNAL_USER = "INTERNAL_USER", _("Internal user")
        SYSTEM = "SYSTEM", _("System")

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
    # Permanent immutable historical snapshot fields — see the "Customer
    # Identity Architecture (Locked)" section of apps/bookings/AGENTS.md.
    # These are used by receipts, audit snapshots, dashboard calendar
    # display, and transaction denormalization and must never be replaced
    # by the identity FKs below, which may change/resolve independently.
    customer_name = models.CharField(max_length=255)
    customer_phone = PhoneNumberField()
    # Resolved club-local player identity. Populated automatically by
    # create_booking() via find_or_create_player_profile() +
    # get_or_create_club_player() — the frontend never supplies this.
    # Booking belongs to a ClubPlayer, not directly to a PlayerProfile;
    # apps.players.models.ClubPlayer.player_profile is the transitive path
    # to global customer identity. Nullable because bookings created
    # directly through the ORM/admin/fixtures (outside create_booking())
    # are not required to resolve identity.
    club_player = models.ForeignKey(
        ClubPlayer,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="bookings",
    )
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
    last_status_changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="last_status_changed_bookings",
    )
    last_status_changed_by_type = models.CharField(
        max_length=32,
        choices=LastStatusActorType.choices,
        blank=True,
        null=True,
    )
    created = models.DateTimeField(auto_now_add=True, db_index=True)
    modified = models.DateTimeField(auto_now=True)

    # Authorization Spine v2 resource-query contract
    # (apps/common/authorization/contracts.py). Booking is an operational
    # club+court reservation, not creator-owned: Staff assigned to a court
    # can access every booking on that court regardless of created_by.
    authorization_config = {
        "scopes": {
            "club": {"path": "club"},
            "court": {"path": "court"},
        },
        "default_scope": "court",
        "select_related": (
            "club",
            "court",
            "created_by",
            "last_status_changed_by",
            "previous_recurring_booking",
            "next_recurring_booking",
            "club_player__player_profile",
        ),
        "prefetch_related": (),
    }

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
            models.Index(fields=["club_player"]),
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
        if (
            self.club_player_id
            and self.club_id
            and self.club_player.club_id != self.club_id
        ):
            errors["club_player"] = (
                "Booking club_player must belong to the same club as the booking."
            )
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


class BookingAttempt(models.Model):
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
        related_name="booking_attempts",
    )
    court = models.ForeignKey(
        Court,
        on_delete=models.CASCADE,
        related_name="booking_attempts",
    )
    attempted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="booking_attempts",
    )
    booking = models.ForeignKey(
        Booking,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="attempts",
    )
    client_request_id = models.UUIDField(blank=True, null=True, db_index=True)
    customer_name = models.CharField(max_length=255)
    customer_phone = PhoneNumberField()
    notes = models.TextField(blank=True)
    requested_start = models.DateTimeField()
    requested_end = models.DateTimeField()
    requested_at = models.DateTimeField(db_index=True)
    requested_source = models.CharField(
        max_length=32,
        choices=Booking.Source.choices,
        default=Booking.Source.MANUAL,
        db_index=True,
    )
    requested_recurring = models.BooleanField(default=False)
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
    # Staff attempted_by restriction is applied in BookingAttemptViewSet
    # filter_scoped_queryset() — it is not a Spine scope.
    authorization_config = {
        "scopes": {
            "club": {"path": "club"},
            "court": {"path": "court"},
        },
        "default_scope": "court",
        "select_related": ("club", "court", "attempted_by", "booking"),
        "prefetch_related": (),
    }

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=Q(requested_start__lt=models.F("requested_end")),
                name="booking_attempt_requested_start_before_end",
            ),
            models.CheckConstraint(
                check=(
                    Q(
                        outcome="SUCCESS",
                        booking__isnull=False,
                        failure_code="",
                    )
                    | (
                        Q(outcome="REJECTED", booking__isnull=True)
                        & ~Q(failure_code="")
                    )
                ),
                name="booking_attempt_outcome_consistent",
            ),
            models.CheckConstraint(
                check=(
                    Q(requested_source="RECURRING", requested_recurring=True)
                    | (~Q(requested_source="RECURRING") & Q(requested_recurring=False))
                ),
                name="booking_attempt_source_matches_recurring_intent",
            ),
            models.CheckConstraint(
                check=~Q(
                    outcome="SUCCESS",
                    resolution="DISMISSED",
                ),
                name="booking_attempt_success_not_dismissed",
            ),
            models.UniqueConstraint(
                fields=["club", "client_request_id"],
                condition=Q(client_request_id__isnull=False),
                name="booking_attempt_client_request_once_per_club",
            ),
        ]
        indexes = [
            models.Index(fields=["club", "created"]),
            models.Index(fields=["club", "outcome", "created"]),
            models.Index(fields=["attempted_by", "created"]),
            models.Index(fields=["court", "created"]),
            models.Index(fields=["club", "requested_start"]),
        ]

    def __str__(self) -> str:
        return f"{self.court} attempt {self.client_request_id}"

    def clean(self):
        super().clean()
        errors = {}
        if (
            self.requested_start
            and self.requested_end
            and self.requested_start >= self.requested_end
        ):
            errors["requested_end"] = "requested_end must be after requested_start."
        if self.requested_recurring != (
            self.requested_source == Booking.Source.RECURRING
        ):
            errors["requested_recurring"] = (
                "Booking attempt recurrence intent must match requested source."
            )
        if self.court_id and self.club_id and self.court.club_id != self.club_id:
            errors["court"] = "Booking attempt court must belong to the club."
        if self.booking_id:
            if self.club_id and self.booking.club_id != self.club_id:
                errors["booking"] = (
                    "Booking attempt booking must belong to the same club."
                )
            if self.court_id and self.booking.court_id != self.court_id:
                errors["booking"] = (
                    "Booking attempt booking must belong to the same court."
                )
            if self.requested_recurring != (
                self.booking.source == Booking.Source.RECURRING
            ):
                errors["requested_recurring"] = (
                    "Booking attempt recurrence intent must match the booking."
                )
            if self.requested_source != self.booking.source:
                errors["requested_source"] = (
                    "Booking attempt requested source must match the booking source."
                )
        if self.outcome == self.Outcome.SUCCESS:
            if self.booking_id is None:
                errors["booking"] = "Successful booking attempts require a booking."
            if self.resolution == self.Resolution.DISMISSED:
                errors["resolution"] = (
                    "Successful booking attempts cannot be dismissed."
                )
            if self.failure_code:
                errors["failure_code"] = (
                    "Successful booking attempts must not have a failure code."
                )
        if self.outcome == self.Outcome.REJECTED:
            if self.booking_id is not None:
                errors["booking"] = "Rejected booking attempts cannot link a booking."
            if not self.failure_code:
                errors["failure_code"] = (
                    "Rejected booking attempts require a failure code."
                )
        if errors:
            raise ValidationError(errors)
