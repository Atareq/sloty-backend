from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Count, DecimalField, Min, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied

from apps.audit.services import record_audit_log, settlement_audit_snapshot
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
ZERO = Decimal("0.00")
MONEY_FIELD = DecimalField(max_digits=10, decimal_places=2)


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


def get_current_unsettled_transactions(
    *,
    access,
    collected_by=None,
    court=None,
    lock=False,
):
    if court is not None and not access.can_access_court(court):
        raise PermissionDenied("You cannot access this court.")
    queryset = access.scoped_transactions_queryset().filter(
        club=access.club,
        created_by__isnull=False,
        settlement_line__isnull=True,
        is_cancelled=False,
    )
    if collected_by is not None:
        queryset = queryset.filter(created_by=collected_by)
    if court is not None:
        queryset = queryset.filter(court=court)
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    return queryset.select_related("booking", "club", "court", "created_by").order_by(
        "created", "id"
    )


def serialize_preview_transactions(transactions):
    return [
        {
            "id": transaction_obj.id,
            "kind": transaction_obj.transaction_type,
            "booking": transaction_obj.booking_id,
            "booking_customer_name": transaction_obj.booking.customer_name,
            "booking_customer_phone": transaction_obj.booking.customer_phone,
            "booking_start_time": transaction_obj.booking.start_time,
            "booking_end_time": transaction_obj.booking.end_time,
            "court": transaction_obj.court_id,
            "court_name": transaction_obj.court.name,
            "amount": transaction_obj.amount,
            "payment_method": transaction_obj.payment_method,
            "payment_reference": transaction_obj.payment_reference,
            "created": transaction_obj.created,
        }
        for transaction_obj in transactions
    ]


def empty_payment_method_totals():
    return {
        str(payment_method): ZERO
        for payment_method, _label in Transaction.PaymentMethod.choices
    }


def summarize_current_custody_transactions(transactions, *, period_end=None):
    transactions = list(transactions)
    booking_payments = ZERO
    booking_refunds = ZERO
    totals_by_payment_method = empty_payment_method_totals()
    for transaction_obj in transactions:
        if transaction_obj.transaction_type == Transaction.Type.PAYMENT:
            booking_payments += transaction_obj.amount
        elif transaction_obj.transaction_type == Transaction.Type.REFUND:
            booking_refunds += transaction_obj.amount
        totals_by_payment_method[
            str(transaction_obj.payment_method)
        ] += transaction_obj.amount
    return {
        "transaction_count": len(transactions),
        "booking_payments": booking_payments,
        "booking_refunds": booking_refunds,
        "net_amount": booking_payments + booking_refunds,
        "totals_by_payment_method": totals_by_payment_method,
        "period_start": min(
            (transaction_obj.created for transaction_obj in transactions),
            default=None,
        ),
        "period_end": period_end or timezone.now(),
    }


def money_total(expression, *, filter=None):
    return Coalesce(
        Sum(expression, filter=filter),
        Value(ZERO),
        output_field=MONEY_FIELD,
    )


def current_custody_aggregate_expressions():
    return {
        "transaction_count": Count("id"),
        "collector_count": Count("created_by", distinct=True),
        "net_amount": money_total("amount"),
        "booking_payments": money_total(
            "amount",
            filter=Q(transaction_type=Transaction.Type.PAYMENT),
        ),
        "booking_refunds": money_total(
            "amount",
            filter=Q(transaction_type=Transaction.Type.REFUND),
        ),
        "period_start": Min("created"),
    }


def aggregate_current_custody(queryset, *, period_end=None):
    summary = queryset.order_by().aggregate(**current_custody_aggregate_expressions())
    summary["net_amount"] = summary["net_amount"] or ZERO
    summary["booking_payments"] = summary["booking_payments"] or ZERO
    summary["booking_refunds"] = summary["booking_refunds"] or ZERO
    summary["period_end"] = period_end or timezone.now()
    return summary


def build_settlement_summary(
    *,
    access,
    collected_by,
    actor,
    queryset,
    court=None,
):
    transactions = list(queryset)
    custody = summarize_current_custody_transactions(transactions)
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
        "period_start": custody["period_start"],
        "period_end": custody["period_end"],
        "transaction_count": custody["transaction_count"],
        "total_amount": custody["net_amount"],
        "booking_payments": custody["booking_payments"],
        "booking_refunds": custody["booking_refunds"],
        "net_amount": custody["net_amount"],
        "totals_by_payment_method": custody["totals_by_payment_method"],
        "transactions": serialize_preview_transactions(transactions),
    }


def build_settlement_preview(*, access, collected_by, actor, court=None):
    validate_preview_collected_by(
        access=access,
        collected_by=collected_by,
        actor=actor,
    )
    queryset = get_current_unsettled_transactions(
        access=access,
        collected_by=collected_by,
        court=court,
        lock=False,
    )
    candidates = list(queryset)
    if not candidates:
        raise SlotyAPIException(
            status_code=status.HTTP_409_CONFLICT,
            code="NO_UNSETTLED_TRANSACTIONS",
            message=NO_UNSETTLED_TRANSACTIONS_MESSAGE,
        )
    return build_settlement_summary(
        access=access,
        collected_by=collected_by,
        actor=actor,
        queryset=candidates,
        court=court,
    )


def preview_settlement(*, access, collected_by, actor, court=None):
    return build_settlement_preview(
        access=access,
        collected_by=collected_by,
        actor=actor,
        court=court,
    )


def build_current_custody_collector_rows(queryset, *, period_end=None):
    period_end = period_end or timezone.now()
    grouped_rows = list(
        queryset.values(
            "created_by",
            "created_by__first_name",
            "created_by__last_name",
            "created_by__username",
        )
        .annotate(**current_custody_aggregate_expressions())
        .order_by("created_by")
    )
    collector_ids = [row["created_by"] for row in grouped_rows]
    method_totals_by_collector = {
        collector_id: empty_payment_method_totals() for collector_id in collector_ids
    }
    if collector_ids:
        for item in (
            queryset.filter(created_by_id__in=collector_ids)
            .order_by()
            .values("created_by", "payment_method")
            .annotate(total=money_total("amount"))
        ):
            method_totals_by_collector[item["created_by"]][
                str(item["payment_method"])
            ] = (item["total"] or ZERO)

    results = []
    for row in grouped_rows:
        collector_id = row["created_by"]
        collector_name = (
            f"{row['created_by__first_name']} {row['created_by__last_name']}"
        ).strip() or row["created_by__username"]
        results.append(
            {
                "collected_by": collector_id,
                "collected_by_name": collector_name,
                "period_start": row["period_start"],
                "period_end": period_end,
                "transaction_count": row["transaction_count"],
                "total_amount": row["net_amount"] or ZERO,
                "net_amount": row["net_amount"] or ZERO,
                "booking_payments": row["booking_payments"] or ZERO,
                "booking_refunds": row["booking_refunds"] or ZERO,
                "totals_by_payment_method": method_totals_by_collector[collector_id],
            }
        )
    return results


def build_unsettled_collector_summaries(
    *,
    access,
    actor,
    collected_by=None,
    court=None,
):
    if collected_by is not None:
        validate_preview_collected_by(
            access=access,
            collected_by=collected_by,
            actor=actor,
        )
    queryset = get_current_unsettled_transactions(
        access=access,
        collected_by=collected_by,
        court=court,
    )
    grouped_rows = build_current_custody_collector_rows(queryset)
    collector_ids = [row["collected_by"] for row in grouped_rows]
    roles_by_user_id = access.active_roles_by_user_ids(collector_ids)
    results = []
    for row in grouped_rows:
        collector_id = row["collected_by"]
        roles = roles_by_user_id.get(collector_id, set())
        if not access.can_preview_settlement_for_roles(
            user_id=collector_id,
            roles=roles,
        ):
            continue
        results.append(
            row
            | {
                "is_self": bool(actor and collector_id == actor.id),
                "can_approve": access.can_approve_settlement_for_roles(
                    user_id=collector_id,
                    roles=roles,
                ),
            }
        )
    results.sort(
        key=lambda item: (item["collected_by_name"].casefold(), item["collected_by"])
    )
    return {"results": results}


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
                get_current_unsettled_transactions(
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

            custody = summarize_current_custody_transactions(candidates)
            settled_at = timezone.now()
            created_settlement = Settlement.objects.create(
                club=access.club,
                court=court,
                collected_by=collected_by,
                period_start=custody["period_start"],
                period_end=custody["period_end"],
                status=Settlement.Status.SETTLED,
                total_amount=custody["net_amount"],
                transaction_count=custody["transaction_count"],
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

            record_audit_log(
                club=created_settlement.club,
                court=created_settlement.court,
                actor=actor,
                action=AuditLog.Action.SETTLEMENT_CREATED,
                entity_type="Settlement",
                entity_id=created_settlement.id,
                after_data=settlement_audit_snapshot(created_settlement)
                | {
                    "booking_payments": str(custody["booking_payments"]),
                    "booking_refunds": str(custody["booking_refunds"]),
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

        before_data = settlement_audit_snapshot(locked_settlement)
        locked_settlement.status = Settlement.Status.SETTLED
        locked_settlement.settled_by = actor
        locked_settlement.settled_at = timezone.now()
        locked_settlement.save(
            update_fields=["status", "settled_by", "settled_at", "modified"]
        )
        from apps.audit.models import AuditLog

        record_audit_log(
            club=locked_settlement.club,
            court=locked_settlement.court,
            actor=actor,
            action=AuditLog.Action.SETTLEMENT_MARKED_SETTLED,
            entity_type="Settlement",
            entity_id=locked_settlement.id,
            before_data=before_data,
            after_data=settlement_audit_snapshot(locked_settlement),
        )
        return locked_settlement
