from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied

from apps.audit.models import AuditLog
from apps.audit.services import (
    booking_audit_snapshot,
    record_audit_log,
    transaction_audit_snapshot,
)
from apps.bookings.models import Booking
from apps.common.exceptions import SlotyAPIException
from apps.transactions.models import Transaction, TransactionAttempt

DUPLICATE_PAYMENT_REFERENCE_MESSAGE = (
    "This payment reference already exists for this club."
)
PAYMENT_ALREADY_CANCELLED_MESSAGE = _("This payment is already cancelled.")
PAYMENT_SETTLED_CANNOT_BE_CANCELLED_MESSAGE = _("Settled payments cannot be cancelled.")
PAYMENT_TERMINAL_BOOKING_CANNOT_BE_CANCELLED_MESSAGE = _(
    "Payments on terminal bookings cannot be cancelled."
)
REFUND_TRANSACTION_CANNOT_BE_CANCELLED_MESSAGE = _(
    "Refund transactions cannot be cancelled."
)
TRANSACTION_BOOKING_NOT_IN_CLUB_MESSAGE = _("Booking must belong to the selected club.")
TRANSACTION_BOOKING_LOCKED_MESSAGE = _(
    "Transactions can only be added to HOLD or CONFIRMED bookings."
)
FIRST_PAYMENT_MINIMUM_DEPOSIT_MESSAGE = _(
    "The first payment must be at least the court minimum deposit."
)
TRANSACTION_AMOUNT_EXCEEDS_REMAINING_MESSAGE = _(
    "Transaction amount cannot exceed remaining booking amount."
)
TRANSACTION_CLIENT_REQUEST_MISMATCH_MESSAGE = _(
    "This client_request_id was already used for a different transaction request."
)
PREVIOUS_TRANSACTION_ATTEMPT_REJECTED_MESSAGE = _(
    "This payment attempt was already rejected."
)
TRANSACTION_ATTEMPT_CANNOT_BE_DISMISSED_MESSAGE = _(
    "Only rejected payment attempts without a transaction can be dismissed."
)
PAYMENT_REFERENCE_REQUIRED_MESSAGE = "Payment reference is required for this court."
PAYMENT_AMOUNT_EXCEEDS_REMAINING_CODE = "PAYMENT_AMOUNT_EXCEEDS_REMAINING"
FIRST_PAYMENT_MINIMUM_DEPOSIT_CODE = "FIRST_PAYMENT_MINIMUM_DEPOSIT_REQUIRED"
PAYMENT_REFERENCE_REQUIRED_CODE = "PAYMENT_REFERENCE_REQUIRED"
DUPLICATE_PAYMENT_REFERENCE_CODE = "DUPLICATE_PAYMENT_REFERENCE"
TRANSACTION_VALIDATION_ERROR_CODE = "VALIDATION_ERROR"
TRANSACTION_ATTEMPT_FAILURE_MESSAGES = {
    PAYMENT_AMOUNT_EXCEEDS_REMAINING_CODE: TRANSACTION_AMOUNT_EXCEEDS_REMAINING_MESSAGE,
    FIRST_PAYMENT_MINIMUM_DEPOSIT_CODE: FIRST_PAYMENT_MINIMUM_DEPOSIT_MESSAGE,
    PAYMENT_REFERENCE_REQUIRED_CODE: PAYMENT_REFERENCE_REQUIRED_MESSAGE,
    DUPLICATE_PAYMENT_REFERENCE_CODE: DUPLICATE_PAYMENT_REFERENCE_MESSAGE,
    TRANSACTION_VALIDATION_ERROR_CODE: _("Invalid transaction data."),
    "TRANSACTION_BOOKING_LOCKED": TRANSACTION_BOOKING_LOCKED_MESSAGE,
    "TRANSACTION_BOOKING_NOT_IN_CLUB": TRANSACTION_BOOKING_NOT_IN_CLUB_MESSAGE,
}
TRANSACTION_ATTEMPT_VALIDATION_FAILURE_CODES = {
    PAYMENT_AMOUNT_EXCEEDS_REMAINING_CODE,
    FIRST_PAYMENT_MINIMUM_DEPOSIT_CODE,
    PAYMENT_REFERENCE_REQUIRED_CODE,
    DUPLICATE_PAYMENT_REFERENCE_CODE,
    TRANSACTION_VALIDATION_ERROR_CODE,
}


def normalize_payment_reference(payment_reference):
    return (payment_reference or "").strip()


def paid_amount_annotation(relation="transactions"):
    return Coalesce(
        Sum(
            f"{relation}__amount",
            filter=Q(
                **{
                    f"{relation}__is_cancelled": False,
                    f"{relation}__transaction_type": Transaction.Type.PAYMENT,
                }
            ),
        ),
        Value(Decimal("0.00")),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )


def annotate_booking_paid_amount(queryset, *, relation="transactions"):
    return queryset.annotate(paid_amount=paid_amount_annotation(relation))


def get_booking_paid_amount(booking, *, include_cancelled=False) -> Decimal:
    queryset = Transaction.objects.filter(
        booking=booking,
        transaction_type=Transaction.Type.PAYMENT,
    )
    if not include_cancelled:
        queryset = queryset.filter(is_cancelled=False)
    return queryset.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")


def get_booking_refunded_amount(booking, *, include_cancelled=False) -> Decimal:
    queryset = Transaction.objects.filter(
        booking=booking,
        transaction_type=Transaction.Type.REFUND,
    )
    if not include_cancelled:
        queryset = queryset.filter(is_cancelled=False)
    total = queryset.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    return abs(total)


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


def is_transaction_client_request_integrity_error(exc):
    message = str(exc).lower()
    return "client_request_id" in message and (
        "unique" in message or "unique_transaction_client_request_per_club" in message
    )


def get_transaction_by_client_request_id(*, club, client_request_id, lock=False):
    if client_request_id is None:
        return None
    queryset = Transaction.objects.filter(
        club=club,
        client_request_id=client_request_id,
    ).select_related("booking", "club", "court", "created_by")
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    return queryset.first()


def transaction_matches_client_request(
    transaction_obj,
    *,
    booking,
    amount,
    payment_method,
    payment_reference,
    notes,
    created_by,
    occurred_at=None,
    occurred_at_provided=False,
):
    expected_notes = notes or ""
    expected_reference = normalize_payment_reference(payment_reference)
    same_request = (
        transaction_obj.transaction_type == Transaction.Type.PAYMENT
        and transaction_obj.booking_id == booking.id
        and transaction_obj.amount == amount
        and transaction_obj.payment_method == payment_method
        and transaction_obj.payment_reference == expected_reference
        and (transaction_obj.notes or "") == expected_notes
        and transaction_obj.created_by_id == getattr(created_by, "id", None)
    )
    if not same_request:
        return False
    if occurred_at_provided:
        return transaction_obj.occurred_at == occurred_at
    return True


def raise_transaction_client_request_mismatch(
    *,
    client_request_id,
    existing_transaction=None,
    existing_attempt=None,
):
    details = {"client_request_id": str(client_request_id)}
    if existing_transaction is not None:
        details["existing_transaction_id"] = existing_transaction.id
    elif existing_attempt is not None:
        details["existing_attempt_id"] = existing_attempt.id
    raise SlotyAPIException(
        status_code=status.HTTP_409_CONFLICT,
        code="TRANSACTION_CLIENT_REQUEST_MISMATCH",
        message=TRANSACTION_CLIENT_REQUEST_MISMATCH_MESSAGE,
        details=details,
    )


def resolve_idempotent_transaction_request(
    *,
    club,
    client_request_id,
    booking,
    amount,
    payment_method,
    payment_reference,
    notes,
    created_by,
    occurred_at=None,
    occurred_at_provided=False,
    lock=False,
):
    existing_transaction = get_transaction_by_client_request_id(
        club=club,
        client_request_id=client_request_id,
        lock=lock,
    )
    if existing_transaction is None:
        return None

    if transaction_matches_client_request(
        existing_transaction,
        booking=booking,
        amount=amount,
        payment_method=payment_method,
        payment_reference=payment_reference,
        notes=notes,
        created_by=created_by,
        occurred_at=occurred_at,
        occurred_at_provided=occurred_at_provided,
    ):
        existing_transaction._sloty_idempotency_reused = True
        return existing_transaction

    raise_transaction_client_request_mismatch(
        client_request_id=client_request_id,
        existing_transaction=existing_transaction,
    )


def transaction_attempt_payload(
    *,
    access,
    booking,
    amount,
    payment_method,
    payment_reference,
    notes,
    created_by,
    client_request_id,
    occurred_at,
):
    return {
        "club": access.club,
        "court": booking.court,
        "booking": booking,
        "attempted_by": created_by,
        "client_request_id": client_request_id,
        "amount": amount,
        "payment_method": payment_method,
        "payment_reference": normalize_payment_reference(payment_reference),
        "notes": notes or "",
        "occurred_at": occurred_at,
    }


def transaction_attempt_matches_client_request(
    attempt,
    *,
    booking,
    amount,
    payment_method,
    payment_reference,
    notes,
    created_by,
    occurred_at=None,
    occurred_at_provided=False,
):
    expected_reference = normalize_payment_reference(payment_reference)
    same_request = (
        attempt.booking_id == booking.id
        and attempt.amount == amount
        and attempt.payment_method == payment_method
        and attempt.payment_reference == expected_reference
        and (attempt.notes or "") == (notes or "")
        and attempt.attempted_by_id == getattr(created_by, "id", None)
    )
    if not same_request:
        return False
    if occurred_at_provided:
        return attempt.occurred_at == occurred_at
    return True


def raise_transaction_attempt_mismatch(*, client_request_id, attempt):
    raise_transaction_client_request_mismatch(
        client_request_id=client_request_id,
        existing_attempt=attempt,
    )


def raise_previous_transaction_attempt_rejection(attempt):
    if attempt.failure_code in TRANSACTION_ATTEMPT_VALIDATION_FAILURE_CODES:
        raise serializers.ValidationError(
            attempt.failure_details
            or {
                "non_field_errors": [
                    str(
                        TRANSACTION_ATTEMPT_FAILURE_MESSAGES.get(
                            attempt.failure_code,
                            PREVIOUS_TRANSACTION_ATTEMPT_REJECTED_MESSAGE,
                        )
                    )
                ]
            }
        )
    raise SlotyAPIException(
        status_code=status.HTTP_409_CONFLICT,
        code=attempt.failure_code,
        message=TRANSACTION_ATTEMPT_FAILURE_MESSAGES.get(
            attempt.failure_code,
            PREVIOUS_TRANSACTION_ATTEMPT_REJECTED_MESSAGE,
        ),
        details=attempt.failure_details or {},
    )


def resolve_idempotent_transaction_attempt(
    *,
    access,
    booking,
    amount,
    payment_method,
    payment_reference,
    notes,
    created_by,
    client_request_id,
    occurred_at=None,
    occurred_at_provided=False,
):
    if client_request_id is None:
        return None

    attempt = (
        TransactionAttempt.objects.select_for_update(of=("self",))
        .filter(club=access.club, client_request_id=client_request_id)
        .select_related("booking", "club", "court", "attempted_by", "transaction")
        .first()
    )
    if attempt is None:
        return None

    if not transaction_attempt_matches_client_request(
        attempt,
        booking=booking,
        amount=amount,
        payment_method=payment_method,
        payment_reference=payment_reference,
        notes=notes,
        created_by=created_by,
        occurred_at=occurred_at,
        occurred_at_provided=occurred_at_provided,
    ):
        raise_transaction_attempt_mismatch(
            client_request_id=client_request_id,
            attempt=attempt,
        )
    if attempt.outcome == TransactionAttempt.Outcome.SUCCESS:
        transaction_obj = attempt.transaction
        transaction_obj._sloty_idempotency_reused = True
        return transaction_obj
    raise_previous_transaction_attempt_rejection(attempt)


def create_success_transaction_attempt(
    *,
    transaction_obj,
    access,
    booking,
    amount,
    payment_method,
    payment_reference,
    notes,
    created_by,
    client_request_id,
    occurred_at,
):
    if client_request_id is not None:
        existing_attempt = TransactionAttempt.objects.filter(
            club=access.club,
            client_request_id=client_request_id,
        ).first()
        if existing_attempt is not None:
            return existing_attempt
    return TransactionAttempt.objects.create(
        **transaction_attempt_payload(
            access=access,
            booking=booking,
            amount=amount,
            payment_method=payment_method,
            payment_reference=payment_reference,
            notes=notes,
            created_by=created_by,
            client_request_id=client_request_id,
            occurred_at=occurred_at,
        ),
        transaction=transaction_obj,
        outcome=TransactionAttempt.Outcome.SUCCESS,
        resolution=TransactionAttempt.Resolution.RESOLVED,
    )


def normalize_failure_details(value):
    if isinstance(value, dict):
        return {
            str(key): normalize_failure_details(item) for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [normalize_failure_details(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def first_error_text(value):
    if isinstance(value, dict):
        for item in value.values():
            text = first_error_text(item)
            if text:
                return text
        return ""
    if isinstance(value, (list, tuple)):
        return first_error_text(value[0]) if value else ""
    return str(value)


def transaction_attempt_failure_code_for_exception(exc):
    if isinstance(exc, SlotyAPIException):
        return exc.api_code
    if isinstance(exc, serializers.ValidationError):
        detail = exc.detail
        if isinstance(detail, dict):
            if "amount" in detail:
                amount_error = first_error_text(detail["amount"])
                if amount_error == str(TRANSACTION_AMOUNT_EXCEEDS_REMAINING_MESSAGE):
                    return PAYMENT_AMOUNT_EXCEEDS_REMAINING_CODE
                if amount_error == str(FIRST_PAYMENT_MINIMUM_DEPOSIT_MESSAGE):
                    return FIRST_PAYMENT_MINIMUM_DEPOSIT_CODE
            if "payment_reference" in detail:
                reference_error = first_error_text(detail["payment_reference"])
                if reference_error == str(DUPLICATE_PAYMENT_REFERENCE_MESSAGE):
                    return DUPLICATE_PAYMENT_REFERENCE_CODE
                if reference_error == PAYMENT_REFERENCE_REQUIRED_MESSAGE:
                    return PAYMENT_REFERENCE_REQUIRED_CODE
        return TRANSACTION_VALIDATION_ERROR_CODE
    return "TRANSACTION_REQUEST_REJECTED"


def transaction_attempt_failure_details_for_exception(exc):
    if isinstance(exc, SlotyAPIException):
        return normalize_failure_details(exc.details or {})
    if isinstance(exc, serializers.ValidationError):
        return normalize_failure_details(exc.detail)
    return {}


def should_record_rejected_transaction_attempt(exc):
    if isinstance(exc, SlotyAPIException):
        return exc.api_code != "TRANSACTION_CLIENT_REQUEST_MISMATCH"
    return isinstance(exc, serializers.ValidationError)


def can_record_transaction_attempt(*, access, booking):
    if booking is None or access is None or booking.club_id != access.club.id:
        return False
    if hasattr(access, "can_access_court"):
        return access.can_access_court(booking.court)
    from apps.transactions.authorization import can_access_court

    return can_access_court(access, booking.court)


def record_rejected_transaction_attempt(
    *,
    access,
    booking,
    amount,
    payment_method,
    payment_reference,
    notes,
    created_by,
    client_request_id,
    occurred_at,
    occurred_at_provided,
    exc,
):
    if not should_record_rejected_transaction_attempt(exc):
        return None
    if not can_record_transaction_attempt(access=access, booking=booking):
        return None

    try:
        with transaction.atomic():
            if client_request_id is not None:
                type(access.club).objects.select_for_update().get(pk=access.club.pk)
                existing_attempt = (
                    TransactionAttempt.objects.select_for_update(of=("self",))
                    .filter(club=access.club, client_request_id=client_request_id)
                    .first()
                )
                if existing_attempt is not None:
                    if transaction_attempt_matches_client_request(
                        existing_attempt,
                        booking=booking,
                        amount=amount,
                        payment_method=payment_method,
                        payment_reference=payment_reference,
                        notes=notes,
                        created_by=created_by,
                        occurred_at=occurred_at,
                        occurred_at_provided=occurred_at_provided,
                    ):
                        return existing_attempt
                    raise_transaction_attempt_mismatch(
                        client_request_id=client_request_id,
                        attempt=existing_attempt,
                    )
            return TransactionAttempt.objects.create(
                **transaction_attempt_payload(
                    access=access,
                    booking=booking,
                    amount=amount,
                    payment_method=payment_method,
                    payment_reference=payment_reference,
                    notes=notes,
                    created_by=created_by,
                    client_request_id=client_request_id,
                    occurred_at=occurred_at,
                ),
                outcome=TransactionAttempt.Outcome.REJECTED,
                failure_code=transaction_attempt_failure_code_for_exception(exc),
                failure_details=transaction_attempt_failure_details_for_exception(exc),
            )
    except IntegrityError:
        if client_request_id is None:
            raise
        return TransactionAttempt.objects.filter(
            club=access.club,
            client_request_id=client_request_id,
        ).first()


def dismiss_transaction_attempt(*, access, attempt, actor):
    with transaction.atomic():
        locked_attempt = (
            TransactionAttempt.objects.select_for_update(of=("self",))
            .select_related("club", "court", "booking", "attempted_by", "transaction")
            .get(pk=attempt.pk)
        )
        can_dismiss = (
            access.can_dismiss_transaction_attempt(locked_attempt)
            if hasattr(access, "can_dismiss_transaction_attempt")
            else None
        )
        if can_dismiss is None:
            from apps.transactions.authorization import can_dismiss_transaction_attempt

            can_dismiss = can_dismiss_transaction_attempt(access, locked_attempt)
        if not can_dismiss:
            raise PermissionDenied("You cannot dismiss this payment attempt.")
        if locked_attempt.resolution == TransactionAttempt.Resolution.DISMISSED:
            return locked_attempt
        if (
            locked_attempt.outcome != TransactionAttempt.Outcome.REJECTED
            or locked_attempt.transaction_id is not None
        ):
            raise SlotyAPIException(
                status_code=status.HTTP_409_CONFLICT,
                code="TRANSACTION_ATTEMPT_CANNOT_BE_DISMISSED",
                message=TRANSACTION_ATTEMPT_CANNOT_BE_DISMISSED_MESSAGE,
            )

        locked_attempt.resolution = TransactionAttempt.Resolution.DISMISSED
        locked_attempt.save(update_fields=["resolution", "modified"])
        return locked_attempt


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
    can_create = (
        access.can_create_transaction_for_booking(booking)
        if hasattr(access, "can_create_transaction_for_booking")
        else None
    )
    if can_create is None:
        from apps.transactions.authorization import can_create_transaction_for_booking

        can_create = can_create_transaction_for_booking(access, booking)
    if not can_create:
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
            {"amount": str(TRANSACTION_AMOUNT_EXCEEDS_REMAINING_MESSAGE)}
        )
    if paid_amount == Decimal("0.00"):
        required_deposit = min(booking.court.minimum_deposit, booking.total_price)
        if amount < required_deposit:
            raise serializers.ValidationError(
                {"amount": str(FIRST_PAYMENT_MINIMUM_DEPOSIT_MESSAGE)}
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
    client_request_id=None,
    occurred_at=None,
    occurred_at_provided=False,
    created_by,
):
    occurred_at = occurred_at or timezone.now()
    try:
        try:
            with transaction.atomic():
                locked_booking = (
                    Booking.objects.select_for_update()
                    .select_related("club", "court")
                    .get(pk=booking.pk)
                )

                if client_request_id is not None:
                    type(access.club).objects.select_for_update().get(pk=access.club.pk)
                    existing_attempt_transaction = (
                        resolve_idempotent_transaction_attempt(
                            access=access,
                            booking=locked_booking,
                            amount=amount,
                            payment_method=payment_method,
                            payment_reference=payment_reference,
                            notes=notes,
                            created_by=created_by,
                            client_request_id=client_request_id,
                            occurred_at=occurred_at,
                            occurred_at_provided=occurred_at_provided,
                        )
                    )
                    if existing_attempt_transaction is not None:
                        return existing_attempt_transaction
                    existing_transaction = resolve_idempotent_transaction_request(
                        club=access.club,
                        client_request_id=client_request_id,
                        booking=locked_booking,
                        amount=amount,
                        payment_method=payment_method,
                        payment_reference=payment_reference,
                        notes=notes,
                        created_by=created_by,
                        occurred_at=occurred_at,
                        occurred_at_provided=occurred_at_provided,
                        lock=True,
                    )
                    if existing_transaction is not None:
                        create_success_transaction_attempt(
                            transaction_obj=existing_transaction,
                            access=access,
                            booking=locked_booking,
                            amount=amount,
                            payment_method=payment_method,
                            payment_reference=payment_reference,
                            notes=notes,
                            created_by=created_by,
                            client_request_id=client_request_id,
                            occurred_at=existing_transaction.occurred_at,
                        )
                        return existing_transaction

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
                    transaction_type=Transaction.Type.PAYMENT,
                    amount=amount,
                    client_request_id=client_request_id,
                    payment_method=payment_method,
                    payment_reference=normalized_reference,
                    notes=notes,
                    occurred_at=occurred_at,
                    created_by=created_by,
                )
                created_transaction._sloty_idempotency_reused = False
                create_success_transaction_attempt(
                    transaction_obj=created_transaction,
                    access=access,
                    booking=locked_booking,
                    amount=amount,
                    payment_method=payment_method,
                    payment_reference=normalized_reference,
                    notes=notes,
                    created_by=created_by,
                    client_request_id=client_request_id,
                    occurred_at=occurred_at,
                )
                record_audit_log(
                    club=created_transaction.club,
                    court=created_transaction.court,
                    actor=created_by,
                    action=AuditLog.Action.TRANSACTION_CREATED,
                    entity_type="Transaction",
                    entity_id=created_transaction.id,
                    after_data=transaction_audit_snapshot(created_transaction),
                )

                if locked_booking.status == Booking.Status.HOLD:
                    locked_booking.status = Booking.Status.CONFIRMED
                    locked_booking.save(update_fields=["status", "modified"])
                    locked_booking.last_status_changed_by = created_by
                    locked_booking.last_status_changed_by_type = (
                        Booking.LastStatusActorType.INTERNAL_USER
                        if created_by
                        else None
                    )
                    locked_booking.save(
                        update_fields=[
                            "status",
                            "last_status_changed_by",
                            "last_status_changed_by_type",
                            "modified",
                        ]
                    )

                return created_transaction
        except IntegrityError as exc:
            if is_duplicate_payment_reference_integrity_error(exc):
                raise serializers.ValidationError(
                    {"payment_reference": [DUPLICATE_PAYMENT_REFERENCE_MESSAGE]}
                ) from exc
            client_request_conflict = (
                client_request_id is not None
                and is_transaction_client_request_integrity_error(exc)
            )
            if client_request_conflict:
                existing_transaction = resolve_idempotent_transaction_request(
                    club=access.club,
                    client_request_id=client_request_id,
                    booking=booking,
                    amount=amount,
                    payment_method=payment_method,
                    payment_reference=payment_reference,
                    notes=notes,
                    created_by=created_by,
                    occurred_at=occurred_at,
                    occurred_at_provided=occurred_at_provided,
                    lock=False,
                )
                if existing_transaction is not None:
                    return existing_transaction
            raise
    except (SlotyAPIException, serializers.ValidationError) as exc:
        record_rejected_transaction_attempt(
            access=access,
            booking=booking,
            amount=amount,
            payment_method=payment_method,
            payment_reference=payment_reference,
            notes=notes,
            created_by=created_by,
            client_request_id=client_request_id,
            occurred_at=occurred_at,
            occurred_at_provided=occurred_at_provided,
            exc=exc,
        )
        raise


def recalculate_booking_status_after_transaction_cancel(booking, *, actor=None):
    old_status = booking.status
    if booking.status == Booking.Status.CONFIRMED and get_booking_paid_amount(
        booking
    ) == Decimal("0.00"):
        booking.status = Booking.Status.HOLD
        booking.last_status_changed_by = actor
        booking.last_status_changed_by_type = (
            Booking.LastStatusActorType.INTERNAL_USER if actor else None
        )
        booking.save(
            update_fields=[
                "status",
                "last_status_changed_by",
                "last_status_changed_by_type",
                "modified",
            ]
        )
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

        can_cancel = (
            access.can_cancel_transaction(locked_transaction)
            if hasattr(access, "can_cancel_transaction")
            else None
        )
        if can_cancel is None:
            from apps.transactions.authorization import can_cancel_transaction

            can_cancel = can_cancel_transaction(access, locked_transaction)
        if not can_cancel:
            raise PermissionDenied("You cannot cancel this transaction.")
        if locked_transaction.is_cancelled:
            raise SlotyAPIException(
                status_code=status.HTTP_409_CONFLICT,
                code="PAYMENT_ALREADY_CANCELLED",
                message=PAYMENT_ALREADY_CANCELLED_MESSAGE,
            )
        if locked_transaction.transaction_type == Transaction.Type.REFUND:
            raise SlotyAPIException(
                status_code=status.HTTP_409_CONFLICT,
                code="REFUND_TRANSACTION_CANNOT_BE_CANCELLED",
                message=REFUND_TRANSACTION_CANNOT_BE_CANCELLED_MESSAGE,
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

        transaction_before_data = transaction_audit_snapshot(locked_transaction)
        transaction_before_data["is_cancelled"] = False
        booking_before_data = booking_audit_snapshot(locked_booking)
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
            locked_booking,
            actor=actor,
        )

        record_audit_log(
            club=locked_transaction.club,
            court=locked_transaction.court,
            actor=actor,
            action=AuditLog.Action.TRANSACTION_CANCELLED,
            entity_type="Transaction",
            entity_id=locked_transaction.id,
            before_data=transaction_before_data
            | {"booking_status": old_booking_status},
            after_data=transaction_audit_snapshot(locked_transaction)
            | {
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
                before_data=booking_before_data,
                after_data=booking_audit_snapshot(locked_booking),
                metadata={
                    "source": "transaction_cancel",
                    "transaction_id": locked_transaction.id,
                },
            )
        return locked_transaction
