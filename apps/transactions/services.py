from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied

from apps.audit.models import AuditLog
from apps.audit.services import record_audit_log
from apps.bookings.models import Booking
from apps.common.exceptions import SlotyAPIException
from apps.transactions.models import Transaction

DUPLICATE_PAYMENT_REFERENCE_MESSAGE = (
    "This payment reference already exists for this club."
)
PAYMENT_ALREADY_CANCELLED_MESSAGE = _("This payment is already cancelled.")
PAYMENT_SETTLED_CANNOT_BE_CANCELLED_MESSAGE = _("Settled payments cannot be cancelled.")
PAYMENT_TERMINAL_BOOKING_CANNOT_BE_CANCELLED_MESSAGE = _(
    "Payments on terminal bookings cannot be cancelled."
)
TRANSACTION_BOOKING_NOT_IN_CLUB_MESSAGE = _("Booking must belong to the selected club.")
TRANSACTION_BOOKING_LOCKED_MESSAGE = _(
    "Transactions can only be added to HOLD or CONFIRMED bookings."
)


def normalize_payment_reference(payment_reference):
    return (payment_reference or "").strip()


def get_booking_paid_amount(booking, *, include_cancelled=False) -> Decimal:
    queryset = Transaction.objects.filter(booking=booking)
    if not include_cancelled:
        queryset = queryset.filter(is_cancelled=False)
    return queryset.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")


def get_booking_remaining_amount(booking) -> Decimal:
    remaining_amount = booking.total_price - get_booking_paid_amount(booking)
    return max(remaining_amount, Decimal("0.00"))


def validate_duplicate_payment_reference(*, club, payment_reference):
    if not payment_reference:
        return
    if Transaction.objects.filter(
        club=club,
        payment_reference=payment_reference,
    ).exists():
        raise serializers.ValidationError(
            {"payment_reference": [DUPLICATE_PAYMENT_REFERENCE_MESSAGE]}
        )


def is_duplicate_payment_reference_integrity_error(exc):
    message = str(exc).lower()
    return "payment_reference" in message and (
        "unique" in message or "unique_non_blank_payment_reference_per_club" in message
    )


def validate_booking_transaction_data(
    *,
    access,
    booking,
    amount,
    payment_method,
    payment_reference,
):
    if booking.club_id != access.club.id:
        raise SlotyAPIException(
            status_code=status.HTTP_409_CONFLICT,
            code="TRANSACTION_BOOKING_NOT_IN_CLUB",
            message=TRANSACTION_BOOKING_NOT_IN_CLUB_MESSAGE,
        )
    if not access.can_create_transaction_for_booking(booking):
        raise PermissionDenied("You cannot create transactions for this booking.")
    if booking.status not in {Booking.Status.HOLD, Booking.Status.CONFIRMED}:
        raise SlotyAPIException(
            status_code=status.HTTP_409_CONFLICT,
            code="TRANSACTION_BOOKING_LOCKED",
            message=TRANSACTION_BOOKING_LOCKED_MESSAGE,
        )
    if amount <= 0:
        raise serializers.ValidationError({"amount": "Amount must be greater than 0."})

    normalized_reference = normalize_payment_reference(payment_reference)
    requires_reference = (
        payment_method
        in {
            Transaction.PaymentMethod.DIGITAL_WALLET,
            Transaction.PaymentMethod.BANK_TRANSFER,
        }
        and booking.court.requires_digital_payment_reference
    )
    if requires_reference and not normalized_reference:
        raise serializers.ValidationError(
            {"payment_reference": "Payment reference is required for this court."}
        )

    paid_amount = get_booking_paid_amount(booking)
    if paid_amount + amount > booking.total_price:
        raise serializers.ValidationError(
            {"amount": "Transaction amount cannot exceed remaining booking amount."}
        )

    validate_duplicate_payment_reference(
        club=access.club,
        payment_reference=normalized_reference,
    )
    return normalized_reference


def create_booking_transaction(
    *,
    access,
    booking,
    amount,
    payment_method,
    payment_reference="",
    notes="",
    created_by,
):
    try:
        with transaction.atomic():
            locked_booking = (
                Booking.objects.select_for_update()
                .select_related("club", "court")
                .get(pk=booking.pk)
            )
            normalized_reference = validate_booking_transaction_data(
                access=access,
                booking=locked_booking,
                amount=amount,
                payment_method=payment_method,
                payment_reference=payment_reference,
            )

            created_transaction = Transaction.objects.create(
                club=locked_booking.club,
                court=locked_booking.court,
                booking=locked_booking,
                amount=amount,
                payment_method=payment_method,
                payment_reference=normalized_reference,
                notes=notes,
                created_by=created_by,
            )
            record_audit_log(
                club=created_transaction.club,
                court=created_transaction.court,
                actor=created_by,
                action=AuditLog.Action.TRANSACTION_CREATED,
                entity_type="Transaction",
                entity_id=created_transaction.id,
                after_data={
                    "transaction_id": created_transaction.id,
                    "booking_id": created_transaction.booking_id,
                    "court_id": created_transaction.court_id,
                    "amount": str(created_transaction.amount),
                    "payment_method": created_transaction.payment_method,
                    "payment_reference": created_transaction.payment_reference,
                },
            )

            if locked_booking.status == Booking.Status.HOLD:
                locked_booking.status = Booking.Status.CONFIRMED
                locked_booking.save(update_fields=["status", "modified"])

            return created_transaction
    except IntegrityError as exc:
        if is_duplicate_payment_reference_integrity_error(exc):
            raise serializers.ValidationError(
                {"payment_reference": [DUPLICATE_PAYMENT_REFERENCE_MESSAGE]}
            ) from exc
        raise


def recalculate_booking_status_after_transaction_cancel(booking):
    old_status = booking.status
    if booking.status == Booking.Status.CONFIRMED and get_booking_paid_amount(
        booking
    ) == Decimal("0.00"):
        booking.status = Booking.Status.HOLD
        booking.save(update_fields=["status", "modified"])
    return old_status, booking.status


def cancel_transaction(*, access, transaction_obj, reason, actor):
    reason = (reason or "").strip()
    if not reason:
        raise serializers.ValidationError({"reason": "A cancel reason is required."})

    with transaction.atomic():
        locked_transaction = (
            Transaction.objects.select_for_update(of=("self",))
            .select_related("booking", "club", "court", "created_by", "cancelled_by")
            .get(pk=transaction_obj.pk)
        )
        locked_booking = (
            Booking.objects.select_for_update()
            .select_related("club", "court")
            .get(pk=locked_transaction.booking_id)
        )
        locked_transaction.booking = locked_booking

        if not access.can_cancel_transaction(locked_transaction):
            raise PermissionDenied("You cannot cancel this transaction.")
        if locked_transaction.is_cancelled:
            raise SlotyAPIException(
                status_code=status.HTTP_409_CONFLICT,
                code="PAYMENT_ALREADY_CANCELLED",
                message=PAYMENT_ALREADY_CANCELLED_MESSAGE,
            )
        if Transaction.objects.filter(
            pk=locked_transaction.pk,
            settlement_line__isnull=False,
        ).exists():
            raise SlotyAPIException(
                status_code=status.HTTP_409_CONFLICT,
                code="PAYMENT_SETTLED_CANNOT_BE_CANCELLED",
                message=PAYMENT_SETTLED_CANNOT_BE_CANCELLED_MESSAGE,
            )
        if locked_booking.status in Booking.LOCKED_STATUSES:
            raise SlotyAPIException(
                status_code=status.HTTP_409_CONFLICT,
                code="PAYMENT_BOOKING_LOCKED",
                message=PAYMENT_TERMINAL_BOOKING_CANNOT_BE_CANCELLED_MESSAGE,
            )

        old_booking_status = locked_booking.status
        locked_transaction.is_cancelled = True
        locked_transaction.cancelled_by = actor
        locked_transaction.cancelled_at = timezone.now()
        locked_transaction.cancellation_reason = reason
        locked_transaction.save(
            update_fields=[
                "is_cancelled",
                "cancelled_by",
                "cancelled_at",
                "cancellation_reason",
                "modified",
            ]
        )
        _, new_booking_status = recalculate_booking_status_after_transaction_cancel(
            locked_booking
        )

        record_audit_log(
            club=locked_transaction.club,
            court=locked_transaction.court,
            actor=actor,
            action=AuditLog.Action.TRANSACTION_CANCELLED,
            entity_type="Transaction",
            entity_id=locked_transaction.id,
            before_data={
                "is_cancelled": False,
                "booking_status": old_booking_status,
            },
            after_data={
                "is_cancelled": True,
                "cancelled_by": actor.id,
                "cancelled_at": locked_transaction.cancelled_at.isoformat(),
                "booking_status": new_booking_status,
            },
            metadata={
                "reason": reason,
                "booking_id": locked_transaction.booking_id,
            },
        )
        if old_booking_status != new_booking_status:
            record_audit_log(
                club=locked_booking.club,
                court=locked_booking.court,
                actor=actor,
                action=AuditLog.Action.BOOKING_UPDATED,
                entity_type="Booking",
                entity_id=locked_booking.id,
                before_data={"status": old_booking_status},
                after_data={"status": new_booking_status},
                metadata={
                    "source": "transaction_cancel",
                    "transaction_id": locked_transaction.id,
                },
            )
        return locked_transaction
