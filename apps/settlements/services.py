from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied

from apps.common.exceptions import SlotyAPIException
from apps.settlements.models import Settlement, SettlementTransaction
from apps.transactions.models import Transaction

NO_UNSETTLED_TRANSACTIONS_MESSAGE = _(
    "There are no unsettled transactions for this user."
)
DOUBLE_SETTLEMENT_MESSAGE = _(
    "One or more transactions were already settled. Please retry."
)
SELF_APPROVAL_MESSAGE = _("You cannot approve your own settlement.")
ALREADY_SETTLED_MESSAGE = _("This settlement is already settled.")
INVALID_SETTLEMENT_STATUS_MESSAGE = _(
    "Only pending settlements can be marked as settled."
)


def validate_period(period_start, period_end):
    if period_start >= period_end:
        raise serializers.ValidationError(
            {"period_end": "period_end must be after period_start."}
        )


def validate_settlement_access(*, access, court=None):
    if court is not None and court.club_id != access.club.id:
        raise serializers.ValidationError(
            {"court": "Court must belong to the selected club."}
        )
    if not access.can_create_settlement(court):
        raise PermissionDenied("You cannot manage settlements for this club.")


def get_user_display_name(user):
    full_name = user.get_full_name().strip()
    return full_name or user.username


def validate_collected_by_membership(*, access, collected_by, actor):
    if actor and collected_by.id == actor.id and access.is_platform_admin:
        return
    if not access.user_has_active_membership(collected_by):
        raise serializers.ValidationError(
            {"collected_by": "User must have an active membership in this club."}
        )


def validate_preview_collected_by(*, access, collected_by, actor):
    validate_collected_by_membership(
        access=access,
        collected_by=collected_by,
        actor=actor,
    )
    if not access.can_preview_settlement_for_user(collected_by):
        raise PermissionDenied("You cannot preview settlements for this user.")


def validate_approval_collected_by(*, access, collected_by, actor):
    validate_collected_by_membership(
        access=access,
        collected_by=collected_by,
        actor=actor,
    )
    if (
        actor
        and collected_by.id == actor.id
        and not (access.is_platform_admin or access.is_owner)
    ):
        raise SlotyAPIException(
            status_code=status.HTTP_403_FORBIDDEN,
            code="SELF_SETTLEMENT_APPROVAL_FORBIDDEN",
            message=SELF_APPROVAL_MESSAGE,
        )
    if not access.can_approve_settlement_for_user(collected_by):
        raise PermissionDenied("You cannot approve settlements for this user.")


def get_settlement_candidate_transactions(*, access, collected_by, lock=False):
    queryset = access.scoped_transactions_queryset().filter(
        club=access.club,
        created_by=collected_by,
        amount__gt=0,
        settlement_line__isnull=True,
        is_cancelled=False,
    )
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    return queryset.select_related("booking", "club", "court", "created_by").order_by(
        "created", "id"
    )


def get_filtered_settlement_candidate_transactions(
    *,
    access,
    collected_by,
    court=None,
    lock=False,
):
    if court is not None and not access.can_access_court(court):
        raise PermissionDenied("You cannot access this court.")
    queryset = get_settlement_candidate_transactions(
        access=access,
        collected_by=collected_by,
        lock=lock,
    )
    if court is not None:
        queryset = queryset.filter(court=court)
    return queryset


def build_totals_by_payment_method(queryset):
    totals = {
        str(payment_method): Decimal("0.00")
        for payment_method, _label in Transaction.PaymentMethod.choices
    }
    for item in (
        queryset.order_by().values("payment_method").annotate(total=Sum("amount"))
    ):
        totals[str(item["payment_method"])] = item["total"] or Decimal("0.00")
    return totals


def serialize_preview_transactions(transactions):
    return [
        {
            "id": transaction_obj.id,
            "booking": transaction_obj.booking_id,
            "court": transaction_obj.court_id,
            "court_name": transaction_obj.court.name,
            "amount": transaction_obj.amount,
            "payment_method": transaction_obj.payment_method,
            "payment_reference": transaction_obj.payment_reference,
            "created": transaction_obj.created,
        }
        for transaction_obj in transactions
    ]


def build_settlement_summary(
    *, access, collected_by, actor, period_start, period_end, queryset, court=None
):
    transactions = list(queryset)
    aggregate = queryset.aggregate(total=Sum("amount"))
    can_approve = access.can_approve_settlement_for_user(collected_by)
    return {
        "club": access.club.id,
        "collected_by": collected_by.id,
        "collected_by_name": get_user_display_name(collected_by),
        "court": court.id if court else None,
        "court_name": court.name if court else "",
        "is_self_preview": bool(actor and collected_by.id == actor.id),
        "can_approve": can_approve,
        "approval_required": not can_approve,
        "period_start": period_start,
        "period_end": period_end,
        "transaction_count": len(transactions),
        "total_amount": aggregate["total"] or Decimal("0.00"),
        "totals_by_payment_method": build_totals_by_payment_method(queryset),
        "transactions": serialize_preview_transactions(transactions),
    }


def build_settlement_preview(*, access, collected_by, actor, court=None):
    validate_preview_collected_by(
        access=access,
        collected_by=collected_by,
        actor=actor,
    )
    queryset = get_filtered_settlement_candidate_transactions(
        access=access,
        collected_by=collected_by,
        court=court,
        lock=False,
    )
    first_transaction = queryset.first()
    if first_transaction is None:
        raise SlotyAPIException(
            status_code=status.HTTP_409_CONFLICT,
            code="NO_UNSETTLED_TRANSACTIONS",
            message=NO_UNSETTLED_TRANSACTIONS_MESSAGE,
        )
    period_start = first_transaction.created
    period_end = timezone.now()
    return build_settlement_summary(
        access=access,
        collected_by=collected_by,
        actor=actor,
        period_start=period_start,
        period_end=period_end,
        queryset=queryset,
        court=court,
    )


def preview_settlement(*, access, collected_by, actor, court=None):
    return build_settlement_preview(
        access=access,
        collected_by=collected_by,
        actor=actor,
        court=court,
    )


def create_approved_settlement(*, access, collected_by, notes="", actor, court=None):
    try:
        with transaction.atomic():
            validate_settlement_access(access=access, court=court)
            validate_approval_collected_by(
                access=access,
                collected_by=collected_by,
                actor=actor,
            )
            candidates = list(
                get_filtered_settlement_candidate_transactions(
                    access=access,
                    collected_by=collected_by,
                    court=court,
                    lock=True,
                ).order_by("created", "id")
            )
            if not candidates:
                raise SlotyAPIException(
                    status_code=status.HTTP_409_CONFLICT,
                    code="NO_UNSETTLED_TRANSACTIONS",
                    message=NO_UNSETTLED_TRANSACTIONS_MESSAGE,
                )

            period_start = candidates[0].created
            period_end = timezone.now()
            settled_at = timezone.now()
            total_amount = sum(
                (transaction_obj.amount for transaction_obj in candidates),
                Decimal("0.00"),
            )
            created_settlement = Settlement.objects.create(
                club=access.club,
                court=court,
                collected_by=collected_by,
                period_start=period_start,
                period_end=period_end,
                status=Settlement.Status.SETTLED,
                total_amount=total_amount,
                transaction_count=len(candidates),
                notes=notes,
                created_by=actor,
                settled_by=actor,
                settled_at=settled_at,
            )
            SettlementTransaction.objects.bulk_create(
                [
                    SettlementTransaction(
                        settlement=created_settlement,
                        transaction=transaction_obj,
                        amount=transaction_obj.amount,
                    )
                    for transaction_obj in candidates
                ]
            )
            from apps.audit.models import AuditLog
            from apps.audit.services import record_audit_log

            record_audit_log(
                club=created_settlement.club,
                court=created_settlement.court,
                actor=actor,
                action=AuditLog.Action.SETTLEMENT_CREATED,
                entity_type="Settlement",
                entity_id=created_settlement.id,
                after_data={
                    "settlement_id": created_settlement.id,
                    "court_id": created_settlement.court_id,
                    "collected_by_id": created_settlement.collected_by_id,
                    "period_start": created_settlement.period_start.isoformat(),
                    "period_end": created_settlement.period_end.isoformat(),
                    "status": created_settlement.status,
                    "settled_by_id": created_settlement.settled_by_id,
                    "settled_at": created_settlement.settled_at.isoformat(),
                    "total_amount": str(created_settlement.total_amount),
                    "transaction_count": created_settlement.transaction_count,
                    "transaction_ids": [
                        transaction_obj.id for transaction_obj in candidates
                    ],
                },
            )
            return created_settlement
    except IntegrityError as exc:
        raise SlotyAPIException(
            status_code=status.HTTP_409_CONFLICT,
            code="SETTLEMENT_CONFLICT",
            message=DOUBLE_SETTLEMENT_MESSAGE,
        ) from exc


def process_settlement_request(
    *,
    access,
    actor,
    collected_by,
    court=None,
    notes="",
):
    return create_approved_settlement(
        access=access,
        collected_by=collected_by,
        court=court,
        notes=notes,
        actor=actor,
    )


def create_settlement(*, access, collected_by, notes="", created_by, court=None):
    return create_approved_settlement(
        access=access,
        collected_by=collected_by,
        court=court,
        notes=notes,
        actor=created_by,
    )


def mark_settlement_settled(*, access, settlement, actor):
    with transaction.atomic():
        locked_settlement = (
            Settlement.objects.select_for_update(of=("self",))
            .select_related(
                "club",
                "court",
                "collected_by",
                "created_by",
                "settled_by",
            )
            .get(pk=settlement.pk)
        )
        if not access.can_access_settlement(locked_settlement):
            raise PermissionDenied("You cannot access this settlement.")
        if not access.can_manage_settlements():
            raise PermissionDenied("You cannot manage settlements for this club.")
        if locked_settlement.status == Settlement.Status.SETTLED:
            raise SlotyAPIException(
                status_code=status.HTTP_409_CONFLICT,
                code="SETTLEMENT_ALREADY_SETTLED",
                message=ALREADY_SETTLED_MESSAGE,
            )
        if locked_settlement.status != Settlement.Status.PENDING:
            raise SlotyAPIException(
                status_code=status.HTTP_409_CONFLICT,
                code="SETTLEMENT_INVALID_STATUS",
                message=INVALID_SETTLEMENT_STATUS_MESSAGE,
            )

        locked_settlement.status = Settlement.Status.SETTLED
        locked_settlement.settled_by = actor
        locked_settlement.settled_at = timezone.now()
        locked_settlement.save(
            update_fields=["status", "settled_by", "settled_at", "modified"]
        )
        from apps.audit.models import AuditLog
        from apps.audit.services import record_audit_log

        record_audit_log(
            club=locked_settlement.club,
            court=locked_settlement.court,
            actor=actor,
            action=AuditLog.Action.SETTLEMENT_MARKED_SETTLED,
            entity_type="Settlement",
            entity_id=locked_settlement.id,
            before_data={"status": Settlement.Status.PENDING},
            after_data={
                "status": Settlement.Status.SETTLED,
                "settled_at": locked_settlement.settled_at.isoformat(),
                "settled_by_id": actor.id if actor else None,
                "collected_by_id": locked_settlement.collected_by_id,
            },
        )
        return locked_settlement
