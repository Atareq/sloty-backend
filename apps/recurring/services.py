from datetime import datetime, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied

from apps.audit.models import AuditLog
from apps.audit.services import record_audit_log
from apps.bookings.models import Booking
from apps.bookings.services import (
    blocking_booking_queryset,
    calculate_booking_price,
    validate_booking_duration,
)
from apps.common.exceptions import SlotyAPIException
from apps.courts.models import Court
from apps.recurring.constants import RECURRING_GENERATION_HORIZON_WEEKS
from apps.recurring.models import RecurringAgreement, RecurringDepositTransaction
from apps.transactions.models import Transaction
from apps.transactions.services import get_booking_paid_amount

RECURRING_POLICY_NOT_CONFIGURED_MESSAGE = _(
    "Recurring deposit refund notice days must be configured on this court."
)
RECURRING_CANCELLATION_HAS_PAID_FUTURE_BOOKINGS_MESSAGE = _(
    "Cancel paid future bookings before cancelling this agreement."
)
RECURRING_DEPOSIT_NOT_REFUNDABLE_MESSAGE = _("This deposit is not eligible for refund.")
RECURRING_REFUND_PERMISSION_DENIED_MESSAGE = _("You cannot refund this deposit.")
RECURRING_AGREEMENT_NOT_ACTIVE_MESSAGE = _("Only active agreements can be cancelled.")
RECURRING_AGREEMENT_NOT_CANCELLED_MESSAGE = _(
    "Deposit refunds are only allowed for cancelled agreements."
)
RECURRING_GENERATION_FAILED_MESSAGE = _("A future occurrence could not be generated.")
RECURRING_DEPOSIT_REFERENCE_DUPLICATE_MESSAGE = _(
    "This payment reference already exists for this club."
)
RECURRING_START_DATE_WEEKDAY_MISMATCH_MESSAGE = _(
    "start_date must match the agreement weekday."
)
BOOKING_SLOT_UNAVAILABLE_MESSAGE = _("The selected booking slot is not available.")


def combine_local_datetime(date_value, time_value):
    return timezone.make_aware(
        datetime.combine(date_value, time_value),
        timezone.get_current_timezone(),
    )


def agreement_occurrence_datetimes(agreement, occurrence_date):
    return (
        combine_local_datetime(occurrence_date, agreement.start_time),
        combine_local_datetime(occurrence_date, agreement.end_time),
    )


def first_weekday_on_or_after(date_value, weekday):
    return date_value + timedelta(days=(weekday - date_value.weekday()) % 7)


def iter_occurrence_dates(*, start_date, weekday, count, stop_before=None):
    current = first_weekday_on_or_after(start_date, weekday)
    yielded = 0
    while yielded < count:
        if stop_before is not None and current >= stop_before:
            break
        yield current
        current += timedelta(days=7)
        yielded += 1


def next_future_occurrence_date(agreement, *, now=None):
    now = now or timezone.now()
    today = timezone.localtime(now).date()
    candidate = first_weekday_on_or_after(
        max(agreement.start_date, today),
        agreement.weekday,
    )
    while True:
        start_time, _end_time = agreement_occurrence_datetimes(agreement, candidate)
        if start_time > now:
            return candidate
        candidate += timedelta(days=7)


def resolve_effective_occurrence_date(agreement, effective_date=None, *, now=None):
    now = now or timezone.now()
    if effective_date is None:
        return next_future_occurrence_date(agreement, now=now)

    if effective_date.weekday() != agreement.weekday:
        raise serializers.ValidationError(
            {"effective_date": "effective_date must match the agreement weekday."}
        )
    if effective_date < timezone.localdate(now):
        raise serializers.ValidationError(
            {"effective_date": "effective_date cannot be in the past."}
        )
    if effective_date < agreement.start_date:
        raise serializers.ValidationError(
            {"effective_date": "effective_date must be on or after start_date."}
        )
    start_time, _end_time = agreement_occurrence_datetimes(agreement, effective_date)
    if start_time <= now:
        raise serializers.ValidationError(
            {
                "effective_date": (
                    "effective_date occurrence start must be in the future."
                )
            }
        )
    return effective_date


def is_deposit_refundable(*, agreement, requested_at, effective_date):
    notice_days = agreement.refund_notice_days_snapshot
    occurrence_start, _end = agreement_occurrence_datetimes(agreement, effective_date)
    if notice_days == 0:
        return requested_at <= occurrence_start
    deadline = occurrence_start - timedelta(days=notice_days)
    return requested_at <= deadline


def normalize_reference(reference):
    return (reference or "").strip()


def validate_duplicate_deposit_reference(*, club, reference):
    if not reference:
        return
    if RecurringDepositTransaction.objects.filter(
        club=club,
        reference=reference,
    ).exists():
        raise serializers.ValidationError(
            {"reference": [str(RECURRING_DEPOSIT_REFERENCE_DUPLICATE_MESSAGE)]}
        )


def is_duplicate_deposit_reference_integrity_error(exc):
    message = str(exc).lower()
    return "reference" in message and (
        "unique" in message
        or "unique_non_blank_recurring_deposit_reference_per_club" in message
    )


def validate_court_recurring_policy(court):
    if court.recurring_deposit_refund_notice_days is None:
        raise SlotyAPIException(
            status_code=status.HTTP_409_CONFLICT,
            code="RECURRING_POLICY_NOT_CONFIGURED",
            message=RECURRING_POLICY_NOT_CONFIGURED_MESSAGE,
        )


def validate_deposit_payment_fields(*, court, payment_method, reference, club):
    normalized = normalize_reference(reference)
    requires_reference = (
        payment_method
        in {
            Transaction.PaymentMethod.DIGITAL_WALLET,
            Transaction.PaymentMethod.BANK_TRANSFER,
        }
        and court.requires_digital_payment_reference
    )
    if requires_reference and not normalized:
        raise serializers.ValidationError(
            {"reference": "Payment reference is required for this court."}
        )
    validate_duplicate_deposit_reference(club=club, reference=normalized)
    return normalized


def create_deposit_transaction(
    *,
    agreement,
    transaction_type,
    amount,
    payment_method,
    reference,
    notes,
    created_by,
):
    return RecurringDepositTransaction.objects.create(
        club=agreement.club,
        court=agreement.court,
        agreement=agreement,
        transaction_type=transaction_type,
        amount=amount,
        payment_method=payment_method,
        reference=reference,
        notes=notes,
        created_by=created_by,
    )


def mark_agreement_generation_failed(
    *, agreement, failure_code, failed_occurrence_start
):
    agreement.status = RecurringAgreement.Status.ACTION_REQUIRED
    agreement.action_required_code = failure_code
    agreement.failed_occurrence_start = failed_occurrence_start
    agreement.action_required_at = timezone.now()
    agreement.save(
        update_fields=[
            "status",
            "action_required_code",
            "failed_occurrence_start",
            "action_required_at",
            "modified",
        ]
    )
    record_audit_log(
        club=agreement.club,
        court=agreement.court,
        actor=None,
        action=AuditLog.Action.RECURRING_GENERATION_FAILED,
        entity_type="RecurringAgreement",
        entity_id=agreement.id,
        after_data={
            "status": agreement.status,
            "action_required_code": agreement.action_required_code,
            "failed_occurrence_start": (
                failed_occurrence_start.isoformat() if failed_occurrence_start else None
            ),
        },
    )


def create_occurrence_booking(*, agreement, occurrence_date, created_by):
    start_time, end_time = agreement_occurrence_datetimes(agreement, occurrence_date)
    validate_booking_duration(agreement.court, start_time, end_time)
    total_price = calculate_booking_price(agreement.court, start_time, end_time)
    if blocking_booking_queryset(agreement.court, start_time, end_time).exists():
        raise SlotyAPIException(
            status_code=status.HTTP_409_CONFLICT,
            code="BOOKING_SLOT_UNAVAILABLE",
            message=BOOKING_SLOT_UNAVAILABLE_MESSAGE,
            details={"failed_occurrence_start": start_time.isoformat()},
        )
    booking = Booking.objects.create(
        club=agreement.club,
        court=agreement.court,
        customer_name=agreement.customer_name,
        customer_phone=agreement.customer_phone,
        start_time=start_time,
        end_time=end_time,
        total_price=total_price,
        status=Booking.Status.CONFIRMED,
        source=Booking.Source.RECURRING,
        recurring_agreement=agreement,
        notes=agreement.notes,
        created_by=created_by,
    )
    record_audit_log(
        club=booking.club,
        court=booking.court,
        actor=created_by,
        action=AuditLog.Action.RECURRING_OCCURRENCE_GENERATED,
        entity_type="Booking",
        entity_id=booking.id,
        after_data={
            "booking_id": booking.id,
            "agreement_id": agreement.id,
            "start_time": booking.start_time.isoformat(),
            "end_time": booking.end_time.isoformat(),
            "total_price": str(booking.total_price),
            "status": booking.status,
        },
    )
    return booking


def generate_occurrences_for_agreement(*, agreement, created_by, stop_before=None):
    created = []
    existing_starts = set(
        Booking.objects.filter(recurring_agreement=agreement).values_list(
            "start_time", flat=True
        )
    )
    for occurrence_date in iter_occurrence_dates(
        start_date=agreement.start_date,
        weekday=agreement.weekday,
        count=RECURRING_GENERATION_HORIZON_WEEKS,
        stop_before=stop_before,
    ):
        start_time, _end = agreement_occurrence_datetimes(agreement, occurrence_date)
        if start_time in existing_starts:
            continue
        try:
            booking = create_occurrence_booking(
                agreement=agreement,
                occurrence_date=occurrence_date,
                created_by=created_by,
            )
        except SlotyAPIException as exc:
            mark_agreement_generation_failed(
                agreement=agreement,
                failure_code=exc.api_code,
                failed_occurrence_start=start_time,
            )
            raise SlotyAPIException(
                status_code=status.HTTP_409_CONFLICT,
                code="RECURRING_GENERATION_FAILED",
                message=RECURRING_GENERATION_FAILED_MESSAGE,
                details={
                    "failed_occurrence_start": start_time.isoformat(),
                    "failure_code": exc.api_code,
                },
            ) from exc
        created.append(booking)
        existing_starts.add(booking.start_time)
    return created


def maintain_rolling_horizon(*, agreement, actor=None):
    if agreement.status != RecurringAgreement.Status.ACTIVE:
        return []
    stop_before = agreement.cancellation_effective_date
    return generate_occurrences_for_agreement(
        agreement=agreement,
        created_by=actor or agreement.created_by,
        stop_before=stop_before,
    )


def maintain_all_rolling_horizons():
    maintained = 0
    failures = 0
    for agreement in RecurringAgreement.objects.filter(
        status=RecurringAgreement.Status.ACTIVE
    ).select_related("club", "court"):
        try:
            with transaction.atomic():
                locked = (
                    RecurringAgreement.objects.select_for_update(of=("self",))
                    .select_related("club", "court")
                    .prefetch_related("court__working_hours__pricing_periods")
                    .get(pk=agreement.pk)
                )
                Court.objects.select_for_update().get(pk=locked.court_id)
                maintain_rolling_horizon(agreement=locked)
                maintained += 1
        except SlotyAPIException:
            failures += 1
    return {"maintained": maintained, "failures": failures}


def preview_recurring_availability(
    *,
    access,
    court,
    weekday,
    start_time,
    end_time,
    start_date,
):
    if not access.can_create_recurring_agreement(court):
        raise PermissionDenied("You cannot access this court.")
    if start_date.weekday() != weekday:
        raise serializers.ValidationError(
            {"start_date": str(RECURRING_START_DATE_WEEKDAY_MISMATCH_MESSAGE)}
        )

    slots = []
    for occurrence_date in iter_occurrence_dates(
        start_date=start_date,
        weekday=weekday,
        count=RECURRING_GENERATION_HORIZON_WEEKS,
    ):
        occurrence_start = combine_local_datetime(occurrence_date, start_time)
        occurrence_end = combine_local_datetime(occurrence_date, end_time)
        available = True
        slot_price = None
        failure_code = None
        try:
            validate_booking_duration(court, occurrence_start, occurrence_end)
            slot_price = calculate_booking_price(
                court, occurrence_start, occurrence_end
            )
            if blocking_booking_queryset(
                court, occurrence_start, occurrence_end
            ).exists():
                available = False
                failure_code = "BOOKING_SLOT_UNAVAILABLE"
        except (serializers.ValidationError, SlotyAPIException) as exc:
            available = False
            failure_code = (
                exc.api_code
                if isinstance(exc, SlotyAPIException)
                else "VALIDATION_ERROR"
            )
        slots.append(
            {
                "date": occurrence_date.isoformat(),
                "start_time": occurrence_start,
                "end_time": occurrence_end,
                "available": available,
                "slot_price": slot_price,
                "failure_code": failure_code,
            }
        )
    return {
        "court": court.id,
        "weekday": weekday,
        "start_time": start_time,
        "end_time": end_time,
        "start_date": start_date.isoformat(),
        "horizon_weeks": RECURRING_GENERATION_HORIZON_WEEKS,
        "slots": slots,
        "all_available": all(slot["available"] for slot in slots),
    }


def create_recurring_agreement(
    *,
    access,
    court,
    customer_name,
    customer_phone,
    weekday,
    start_time,
    end_time,
    start_date,
    payment_method,
    reference="",
    notes="",
    created_by,
):
    if not access.can_create_recurring_agreement(court):
        raise PermissionDenied("You cannot create recurring agreements for this court.")
    if not court.is_active or not court.club.is_active:
        raise serializers.ValidationError({"court": "Court and club must be active."})
    if start_date.weekday() != weekday:
        raise serializers.ValidationError(
            {"start_date": str(RECURRING_START_DATE_WEEKDAY_MISMATCH_MESSAGE)}
        )

    try:
        with transaction.atomic():
            locked_court = (
                Court.objects.select_for_update(of=("self",))
                .select_related("club")
                .prefetch_related("working_hours__pricing_periods")
                .get(pk=court.pk)
            )
            validate_court_recurring_policy(locked_court)

            sample_start = combine_local_datetime(start_date, start_time)
            sample_end = combine_local_datetime(start_date, end_time)
            validate_booking_duration(locked_court, sample_start, sample_end)
            deposit_amount = calculate_booking_price(
                locked_court, sample_start, sample_end
            )
            normalized_reference = validate_deposit_payment_fields(
                court=locked_court,
                payment_method=payment_method,
                reference=reference,
                club=locked_court.club,
            )

            for occurrence_date in iter_occurrence_dates(
                start_date=start_date,
                weekday=weekday,
                count=RECURRING_GENERATION_HORIZON_WEEKS,
            ):
                occurrence_start = combine_local_datetime(occurrence_date, start_time)
                occurrence_end = combine_local_datetime(occurrence_date, end_time)
                validate_booking_duration(
                    locked_court, occurrence_start, occurrence_end
                )
                calculate_booking_price(locked_court, occurrence_start, occurrence_end)
                if blocking_booking_queryset(
                    locked_court, occurrence_start, occurrence_end
                ).exists():
                    raise SlotyAPIException(
                        status_code=status.HTTP_409_CONFLICT,
                        code="BOOKING_SLOT_UNAVAILABLE",
                        message=BOOKING_SLOT_UNAVAILABLE_MESSAGE,
                        details={
                            "failed_occurrence_start": occurrence_start.isoformat(),
                        },
                    )

            now = timezone.now()
            agreement = RecurringAgreement.objects.create(
                club=locked_court.club,
                court=locked_court,
                customer_name=customer_name,
                customer_phone=customer_phone,
                weekday=weekday,
                start_time=start_time,
                end_time=end_time,
                start_date=start_date,
                status=RecurringAgreement.Status.ACTIVE,
                deposit_amount=deposit_amount,
                deposit_status=RecurringAgreement.DepositStatus.HELD,
                refund_notice_days_snapshot=(
                    locked_court.recurring_deposit_refund_notice_days
                ),
                deposit_collected_at=now,
                deposit_collected_by=created_by,
                notes=notes or "",
                created_by=created_by,
            )
            deposit_tx = create_deposit_transaction(
                agreement=agreement,
                transaction_type=RecurringDepositTransaction.Type.COLLECTION,
                amount=deposit_amount,
                payment_method=payment_method,
                reference=normalized_reference,
                notes=notes or "",
                created_by=created_by,
            )
            generate_occurrences_for_agreement(
                agreement=agreement,
                created_by=created_by,
            )
            record_audit_log(
                club=agreement.club,
                court=agreement.court,
                actor=created_by,
                action=AuditLog.Action.RECURRING_AGREEMENT_CREATED,
                entity_type="RecurringAgreement",
                entity_id=agreement.id,
                after_data={
                    "agreement_id": agreement.id,
                    "status": agreement.status,
                    "deposit_status": agreement.deposit_status,
                    "deposit_amount": str(agreement.deposit_amount),
                    "start_date": agreement.start_date.isoformat(),
                    "weekday": agreement.weekday,
                },
            )
            record_audit_log(
                club=agreement.club,
                court=agreement.court,
                actor=created_by,
                action=AuditLog.Action.RECURRING_DEPOSIT_COLLECTED,
                entity_type="RecurringDepositTransaction",
                entity_id=deposit_tx.id,
                after_data={
                    "agreement_id": agreement.id,
                    "amount": str(deposit_tx.amount),
                    "payment_method": deposit_tx.payment_method,
                    "reference": deposit_tx.reference,
                },
            )
            return agreement
    except IntegrityError as exc:
        if is_duplicate_deposit_reference_integrity_error(exc):
            raise serializers.ValidationError(
                {"reference": [str(RECURRING_DEPOSIT_REFERENCE_DUPLICATE_MESSAGE)]}
            ) from exc
        raise


def get_affected_future_bookings(agreement, effective_date):
    effective_start = combine_local_datetime(effective_date, agreement.start_time)
    return Booking.objects.filter(
        recurring_agreement=agreement,
        start_time__gte=effective_start,
        status__in=Booking.BLOCKING_STATUSES,
    ).order_by("start_time", "id")


def get_paid_future_bookings(agreement, effective_date):
    paid = []
    total_paid = Decimal("0.00")
    for booking in get_affected_future_bookings(agreement, effective_date):
        paid_amount = get_booking_paid_amount(booking)
        if paid_amount > 0:
            paid.append(booking)
            total_paid += paid_amount
    return paid, total_paid


def build_cancellation_preview(*, access, agreement, effective_date=None):
    if not access.can_view_recurring_agreement(agreement):
        raise PermissionDenied("You cannot access this recurring agreement.")
    if agreement.status != RecurringAgreement.Status.ACTIVE:
        raise SlotyAPIException(
            status_code=status.HTTP_409_CONFLICT,
            code="RECURRING_AGREEMENT_NOT_ACTIVE",
            message=RECURRING_AGREEMENT_NOT_ACTIVE_MESSAGE,
        )

    now = timezone.now()
    resolved_effective = resolve_effective_occurrence_date(
        agreement, effective_date, now=now
    )
    refundable = is_deposit_refundable(
        agreement=agreement,
        requested_at=now,
        effective_date=resolved_effective,
    )
    paid_bookings, total_paid = get_paid_future_bookings(agreement, resolved_effective)
    return {
        "agreement_id": agreement.id,
        "previewed_at": now,
        "effective_date": resolved_effective,
        "deposit_status_if_cancelled": (
            RecurringAgreement.DepositStatus.REFUND_DUE
            if refundable
            else RecurringAgreement.DepositStatus.FORFEITED
        ),
        "deposit_refundable": refundable,
        "deposit_amount": agreement.deposit_amount,
        "refund_notice_days_snapshot": agreement.refund_notice_days_snapshot,
        "paid_future_bookings": len(paid_bookings),
        "paid_future_booking_ids": [booking.id for booking in paid_bookings],
        "total_paid_amount": total_paid,
        "blocks_cancellation": bool(paid_bookings),
    }


def release_future_occurrences(*, agreement, effective_date, actor, reason):
    cancelled = []
    for booking in get_affected_future_bookings(agreement, effective_date):
        locked = Booking.objects.select_for_update().get(pk=booking.pk)
        if locked.status in Booking.LOCKED_STATUSES:
            continue
        before = {"status": locked.status}
        locked.status = Booking.Status.CANCELLED
        locked.cancellation_reason = reason or "Recurring agreement cancelled."
        locked.cancelled_at = timezone.now()
        locked.save(
            update_fields=[
                "status",
                "cancellation_reason",
                "cancelled_at",
                "modified",
            ]
        )
        record_audit_log(
            club=locked.club,
            court=locked.court,
            actor=actor,
            action=AuditLog.Action.BOOKING_CANCELLED,
            entity_type="Booking",
            entity_id=locked.id,
            before_data=before,
            after_data={"status": locked.status},
            metadata={
                "source": "recurring_agreement_cancel",
                "agreement_id": agreement.id,
            },
        )
        cancelled.append(locked)
    return cancelled


def resolve_deposit_on_cancellation(*, agreement, refundable, actor, now):
    if refundable:
        agreement.deposit_status = RecurringAgreement.DepositStatus.REFUND_DUE
        agreement.refund_due_at = now
        agreement.save(update_fields=["deposit_status", "refund_due_at", "modified"])
        record_audit_log(
            club=agreement.club,
            court=agreement.court,
            actor=actor,
            action=AuditLog.Action.RECURRING_DEPOSIT_REFUND_DUE,
            entity_type="RecurringAgreement",
            entity_id=agreement.id,
            after_data={"deposit_status": agreement.deposit_status},
            metadata={"cancellation_requested_at": now.isoformat()},
        )
        return agreement.deposit_status

    agreement.deposit_status = RecurringAgreement.DepositStatus.FORFEITED
    agreement.save(update_fields=["deposit_status", "modified"])
    record_audit_log(
        club=agreement.club,
        court=agreement.court,
        actor=actor,
        action=AuditLog.Action.RECURRING_DEPOSIT_FORFEITED,
        entity_type="RecurringAgreement",
        entity_id=agreement.id,
        after_data={"deposit_status": agreement.deposit_status},
        metadata={"cancellation_requested_at": now.isoformat()},
    )
    return agreement.deposit_status


def cancel_recurring_agreement(
    *,
    access,
    agreement,
    effective_date=None,
    reason="",
    actor,
):
    if not access.can_cancel_recurring_agreement(agreement):
        raise PermissionDenied("You cannot cancel this recurring agreement.")

    with transaction.atomic():
        locked = (
            RecurringAgreement.objects.select_for_update(of=("self",))
            .select_related("club", "court", "deposit_collected_by")
            .get(pk=agreement.pk)
        )
        Court.objects.select_for_update().get(pk=locked.court_id)
        if locked.status != RecurringAgreement.Status.ACTIVE:
            raise SlotyAPIException(
                status_code=status.HTTP_409_CONFLICT,
                code="RECURRING_AGREEMENT_NOT_ACTIVE",
                message=RECURRING_AGREEMENT_NOT_ACTIVE_MESSAGE,
            )

        now = timezone.now()
        resolved_effective = resolve_effective_occurrence_date(
            locked, effective_date, now=now
        )
        paid_bookings, total_paid = get_paid_future_bookings(locked, resolved_effective)
        if paid_bookings:
            raise SlotyAPIException(
                status_code=status.HTTP_409_CONFLICT,
                code="RECURRING_CANCELLATION_HAS_PAID_FUTURE_BOOKINGS",
                message=RECURRING_CANCELLATION_HAS_PAID_FUTURE_BOOKINGS_MESSAGE,
                details={
                    "booking_ids": [booking.id for booking in paid_bookings],
                    "paid_future_bookings": len(paid_bookings),
                    "total_paid_amount": str(total_paid),
                },
            )

        refundable = is_deposit_refundable(
            agreement=locked,
            requested_at=now,
            effective_date=resolved_effective,
        )
        release_future_occurrences(
            agreement=locked,
            effective_date=resolved_effective,
            actor=actor,
            reason=reason,
        )
        deposit_status = resolve_deposit_on_cancellation(
            agreement=locked,
            refundable=refundable,
            actor=actor,
            now=now,
        )
        locked.status = RecurringAgreement.Status.CANCELLED
        locked.cancellation_requested_at = now
        locked.cancellation_effective_date = resolved_effective
        locked.cancelled_by = actor
        locked.cancellation_reason = (reason or "").strip()
        locked.save(
            update_fields=[
                "status",
                "cancellation_requested_at",
                "cancellation_effective_date",
                "cancelled_by",
                "cancellation_reason",
                "modified",
            ]
        )
        record_audit_log(
            club=locked.club,
            court=locked.court,
            actor=actor,
            action=AuditLog.Action.RECURRING_AGREEMENT_CANCELLED,
            entity_type="RecurringAgreement",
            entity_id=locked.id,
            after_data={
                "status": locked.status,
                "deposit_status": deposit_status,
                "cancellation_effective_date": resolved_effective.isoformat(),
                "cancellation_requested_at": now.isoformat(),
            },
            metadata={"reason": locked.cancellation_reason},
        )
        return locked


def refund_recurring_deposit(
    *,
    access,
    agreement,
    payment_method,
    reference="",
    notes="",
    actor,
):
    if not access.can_refund_recurring_deposit(agreement):
        raise SlotyAPIException(
            status_code=status.HTTP_403_FORBIDDEN,
            code="RECURRING_REFUND_PERMISSION_DENIED",
            message=RECURRING_REFUND_PERMISSION_DENIED_MESSAGE,
        )

    try:
        with transaction.atomic():
            locked = (
                RecurringAgreement.objects.select_for_update(of=("self",))
                .select_related("club", "court")
                .get(pk=agreement.pk)
            )
            if locked.status != RecurringAgreement.Status.CANCELLED:
                raise SlotyAPIException(
                    status_code=status.HTTP_409_CONFLICT,
                    code="RECURRING_AGREEMENT_NOT_CANCELLED",
                    message=RECURRING_AGREEMENT_NOT_CANCELLED_MESSAGE,
                )
            if locked.deposit_status != RecurringAgreement.DepositStatus.REFUND_DUE:
                raise SlotyAPIException(
                    status_code=status.HTTP_409_CONFLICT,
                    code="RECURRING_DEPOSIT_NOT_REFUNDABLE",
                    message=RECURRING_DEPOSIT_NOT_REFUNDABLE_MESSAGE,
                )
            normalized_reference = validate_deposit_payment_fields(
                court=locked.court,
                payment_method=payment_method,
                reference=reference,
                club=locked.club,
            )
            refund_tx = create_deposit_transaction(
                agreement=locked,
                transaction_type=RecurringDepositTransaction.Type.REFUND,
                amount=locked.deposit_amount,
                payment_method=payment_method,
                reference=normalized_reference,
                notes=notes or "",
                created_by=actor,
            )
            locked.deposit_status = RecurringAgreement.DepositStatus.REFUNDED
            locked.refunded_at = timezone.now()
            locked.refunded_by = actor
            locked.save(
                update_fields=[
                    "deposit_status",
                    "refunded_at",
                    "refunded_by",
                    "modified",
                ]
            )
            record_audit_log(
                club=locked.club,
                court=locked.court,
                actor=actor,
                action=AuditLog.Action.RECURRING_DEPOSIT_REFUNDED,
                entity_type="RecurringDepositTransaction",
                entity_id=refund_tx.id,
                after_data={
                    "agreement_id": locked.id,
                    "deposit_status": locked.deposit_status,
                    "amount": str(locked.deposit_amount),
                    "payment_method": refund_tx.payment_method,
                    "reference": refund_tx.reference,
                },
            )
            return locked, refund_tx
    except IntegrityError as exc:
        if is_duplicate_deposit_reference_integrity_error(exc):
            raise serializers.ValidationError(
                {"reference": [str(RECURRING_DEPOSIT_REFERENCE_DUPLICATE_MESSAGE)]}
            ) from exc
        raise
