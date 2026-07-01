from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Sum
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from apps.bookings.models import Booking
from apps.transactions.models import Transaction

DUPLICATE_PAYMENT_REFERENCE_MESSAGE = (
    "This payment reference already exists for this club."
)


def normalize_payment_reference(payment_reference):
    return (payment_reference or "").strip()


def get_booking_paid_amount(booking) -> Decimal:
    return Transaction.objects.filter(booking=booking).aggregate(total=Sum("amount"))[
        "total"
    ] or Decimal("0.00")


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
        raise serializers.ValidationError(
            {"booking": "Booking must belong to the selected club."}
        )
    if not access.can_create_transaction_for_booking(booking):
        raise PermissionDenied("You cannot create transactions for this booking.")
    if booking.status not in {Booking.Status.HOLD, Booking.Status.CONFIRMED}:
        raise serializers.ValidationError(
            {"booking": "Transactions can only be added to HOLD or CONFIRMED bookings."}
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
            from apps.audit.models import AuditLog
            from apps.audit.services import record_audit_log

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
