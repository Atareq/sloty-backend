"""
Settlements & Current Custody Domain Services.

PRIMARY ARCHITECTURAL INVARIANT:
Current Custody and Settlement are financially scoped by CLUB + optional COLLECTOR,
never by COURT.

Financial Custody Scope = Club + optional Collector + unsettled financial transactions.
NOT: Club + Court + Collector.

Responsibilities:
1. Authorization / Scope Guard:
   - validate_preview_authority(*, access, actor, collector)
   - validate_settlement_authority(*, access, actor, collector, court=None)
   Determines whether caller may access requested custody scope.
   Staff are restricted to previewing themselves. Owner / Platform Admin can
   preview any collector.
   Managers are gated by manager_can_settle_transactions.
   Does NOT query transactions or calculate custody.

2. Authoritative Custody Resolution:
   - get_unsettled_transactions_queryset(*, club, collector=None, lock=False)
   - build_custody(*, club, collector=None, lock=False, period_end=None)
   - preview_custody(*, access, actor, collector, court=None)
   Authoritative, side-effect-free financial transaction set resolution.
   Never filters by court and never reuses operational transaction scoping
   (ClubAccessContext.scoped_transactions_queryset).
   collector=None: all unsettled transactions in the club.
   collector=user: all unsettled transactions in the club collected by that
   user across all courts.

3. Settlement Mutation:
   - settle_custody(*, access, actor, collector, notes="", court=None)
   Consumes the exact same authoritative custody query
   (get_unsettled_transactions_queryset),
   locking candidate transactions and mutating settlement records atomically.
"""

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


# =============================================================================
# RESPONSIBILITY 1: AUTHORIZATION / SCOPE GUARD
# =============================================================================


def validate_preview_authority(*, access, actor, collector):
    """
    Authorization guard for current custody preview.

    Validates whether caller (actor) is authorized to preview custody
    for `collector` in `access.club`.

    Rules:
    - Staff may ONLY preview themselves (actor.id == collector.id).
    - Manager may preview if manager has settlement preview authority.
    - Owner and Platform Admin may preview any collector.
    - Collector must have active membership in the club (except Platform Admin
      self-preview).

    This function only performs authorization. It does NOT query transactions
    or calculate custody.
    """
    validate_collected_by_membership(
        access=access,
        collected_by=collector,
        actor=actor,
    )
    if not access.can_preview_settlement_for_user(collector):
        raise PermissionDenied("You cannot preview settlements for this user.")


def validate_settlement_authority(*, access, actor, collector, court=None):
    """
    Authorization guard for settlement mutation.

    Validates whether caller (actor) is authorized to settle custody
    for `collector` in `access.club`.

    Rules:
    - Actor must have settlement creation permission in the club.
    - Staff cannot settle.
    - Self-settlement approval is forbidden unless Owner or Platform Admin.
    - Collector must have active membership in the club.
    """
    if court is not None and court.club_id != access.club.id:
        raise serializers.ValidationError(
            {"court": "Court must belong to the selected club."}
        )
    if not access.can_create_settlement(court):
        raise PermissionDenied("You cannot manage settlements for this club.")

    validate_collected_by_membership(
        access=access,
        collected_by=collector,
        actor=actor,
    )
    if (
        actor
        and collector.id == actor.id
        and not (access.is_platform_admin or access.is_owner)
    ):
        raise SlotyAPIException(
            status_code=status.HTTP_403_FORBIDDEN,
            code="SELF_SETTLEMENT_APPROVAL_FORBIDDEN",
            message=SELF_APPROVAL_MESSAGE,
        )
    if not access.can_approve_settlement_for_user(collector):
        raise PermissionDenied("You cannot approve settlements for this user.")


def validate_settlement_access(*, access, court=None):
    """Legacy backward-compatibility helper."""
    if court is not None and court.club_id != access.club.id:
        raise serializers.ValidationError(
            {"court": "Court must belong to the selected club."}
        )
    if not access.can_create_settlement(court):
        raise PermissionDenied("You cannot manage settlements for this club.")


def validate_preview_collected_by(*, access, collected_by, actor):
    """Legacy alias for validate_preview_authority."""
    return validate_preview_authority(
        access=access,
        actor=actor,
        collector=collected_by,
    )


def validate_approval_collected_by(*, access, collected_by, actor):
    """Legacy alias for approval authorization validation."""
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


# =============================================================================
# RESPONSIBILITY 2: AUTHORITATIVE CUSTODY RESOLUTION
# =============================================================================


def get_unsettled_transactions_queryset(
    *,
    club,
    collector=None,
    lock=False,
):
    """
    Authoritative financial query resolving unsettled transactions.

    ARCHITECTURAL INVARIANT:
    Current Custody is a financial scope, not an operational Court scope.
    Financial scope = Club + optional Collector + unsettled transactions.

    Why Court must NOT be used:
    Custody represents real-world physical or accounted money held by a
    collector for the Club. A collector holding cash holds it for the entire
    club, regardless of which court's booking generated the payment.
    Filtering custody by court would fragment physical cash tracking and
    cause staff self-preview to diverge from owner/manager preview.

    Why normal Transaction authorization (scoped_transactions_queryset)
    must NOT be reused:
    Operational Transaction authorization
    (ClubAccessContext.scoped_transactions_queryset) intentionally applies
    court-level scoping (e.g. staff only see their assigned court's bookings).
    Reusing operational scoping here leaked court restrictions into financial
    calculation, causing staff to miss their own cross-court collections.

    Why preview and settlement must share the same resolver:
    Preview and settlement must originate from the exact same transaction
    set query so that what is previewed is exactly what is settled without
    drift or discrepancy.

    Semantics:
    - collector=None: All unsettled transactions in the Club (e.g. club-wide
      unsettled custody).
    - collector=User: All unsettled transactions in the Club where
      created_by=collector, across EVERY court in the club.
    """
    queryset = Transaction.objects.filter(
        club=club,
        created_by__isnull=False,
        settlement_line__isnull=True,
        is_cancelled=False,
    )
    if collector is not None:
        queryset = queryset.filter(created_by=collector)
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    return queryset.select_related("booking", "club", "court", "created_by").order_by(
        "created", "id"
    )


def get_current_unsettled_transactions(
    *,
    access=None,
    club=None,
    collected_by=None,
    collector=None,
    court=None,
    lock=False,
):
    """
    Query helper for unsettled transactions.

    Current Custody and Settlement use get_unsettled_transactions_queryset,
    which is strictly scoped by Club + optional Collector (NOT Court).

    This helper accepts optional `court` for callers (such as court-filtered
    dashboard metrics) that explicitly request court-specific transaction metrics.
    """
    target_club = club or (access.club if access else None)
    target_collector = collector if collector is not None else collected_by
    queryset = get_unsettled_transactions_queryset(
        club=target_club,
        collector=target_collector,
        lock=lock,
    )
    if court is not None:
        queryset = queryset.filter(court=court)
    return queryset


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


def build_custody(
    *,
    club,
    collector=None,
    lock=False,
    period_end=None,
):
    """
    Authoritative, side-effect-free Current Custody calculation.

    ARCHITECTURAL INVARIANT:
    Current Custody is a financial scope, not an operational Court scope.
    Financial scope = Club + optional Collector + unsettled transactions.

    Why Court must NOT be used:
    A collector's custody contains every unsettled transaction collected by
    that user in this Club, regardless of which Court generated the
    transaction. Filtering by Court fragments cash responsibility and causes
    discrepancies between Staff self-preview and Owner/Manager preview.

    Why operational Transaction authorization must NOT be reused:
    Operational Transaction authorization intentionally applies Court-level
    restrictions (e.g., staff assigned to Court 1 cannot view Court 2
    transactions). Reusing it for custody leaked those restrictions, making
    staff self-preview exclude legitimate collections.

    Why preview and settlement must share this resolver:
    Both preview and settlement query the identical candidates via
    get_unsettled_transactions_queryset to guarantee mathematical and
    transactional consistency.

    Semantics:
    - collector=None: All unsettled transactions in the Club (e.g. for club
      overview).
    - collector=User: All unsettled transactions in the Club collected by
      that specific user.
    """
    queryset = get_unsettled_transactions_queryset(
        club=club,
        collector=collector,
        lock=lock,
    )
    transactions = list(queryset)
    summary = summarize_current_custody_transactions(
        transactions,
        period_end=period_end,
    )
    return summary | {
        "club": club.id if hasattr(club, "id") else club,
        "collected_by": collector.id if collector else None,
        "collected_by_name": get_user_display_name(collector) if collector else "",
        "transactions": transactions,
    }


def preview_custody(
    *,
    access,
    actor,
    collector,
    court=None,
):
    """
    Current Custody preview operation.

    1. Authorization / Scope Guard: validates caller authority for this
       collector.
    2. Authoritative Custody: resolves candidate transactions (Club +
       Collector, NOT Court).
    3. Return calculated preview without side effects.
    """
    validate_preview_authority(
        access=access,
        actor=actor,
        collector=collector,
    )
    custody = build_custody(
        club=access.club,
        collector=collector,
        lock=False,
    )
    if not custody["transactions"]:
        raise SlotyAPIException(
            status_code=status.HTTP_409_CONFLICT,
            code="NO_UNSETTLED_TRANSACTIONS",
            message=NO_UNSETTLED_TRANSACTIONS_MESSAGE,
        )
    can_approve = access.can_approve_settlement_for_user(collector)
    return {
        "club": access.club.id,
        "collected_by": collector.id,
        "collected_by_name": get_user_display_name(collector),
        "court": court.id if court else None,
        "court_name": court.name if court else "",
        "is_self_preview": bool(actor and collector.id == actor.id),
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
        "transactions": serialize_preview_transactions(custody["transactions"]),
    }


def build_settlement_preview(*, access, collected_by, actor, court=None):
    """Legacy alias for preview_custody."""
    return preview_custody(
        access=access,
        actor=actor,
        collector=collected_by,
        court=court,
    )


def preview_settlement(*, access, collected_by, actor, court=None):
    """Legacy alias for preview_custody."""
    return preview_custody(
        access=access,
        actor=actor,
        collector=collected_by,
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
        validate_preview_authority(
            access=access,
            actor=actor,
            collector=collected_by,
        )
    queryset = get_unsettled_transactions_queryset(
        club=access.club,
        collector=collected_by,
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


# =============================================================================
# RESPONSIBILITY 3: SETTLEMENT MUTATION
# =============================================================================


def settle_custody(
    *,
    access,
    actor,
    collector,
    notes="",
    court=None,
):
    """
    Settlement mutation operation.

    1. Authorization / Scope Guard: validates settlement authority.
    2. Authoritative Custody: locks and retrieves the exact same candidate
       set resolved by get_unsettled_transactions_queryset (Club + Collector,
       NOT Court).
    3. Mutation: creates Settlement and SettlementTransaction lines atomically.
    """
    try:
        with transaction.atomic():
            validate_settlement_authority(
                access=access,
                actor=actor,
                collector=collector,
                court=court,
            )
            candidates = list(
                get_unsettled_transactions_queryset(
                    club=access.club,
                    collector=collector,
                    lock=True,
                )
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
                collected_by=collector,
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


def create_approved_settlement(*, access, collected_by, notes="", actor, court=None):
    """Legacy alias for settle_custody."""
    return settle_custody(
        access=access,
        actor=actor,
        collector=collected_by,
        notes=notes,
        court=court,
    )


def process_settlement_request(
    *,
    access,
    actor,
    collected_by,
    court=None,
    notes="",
):
    """Legacy alias for settle_custody."""
    return settle_custody(
        access=access,
        actor=actor,
        collector=collected_by,
        court=court,
        notes=notes,
    )


def create_settlement(*, access, collected_by, notes="", created_by, court=None):
    """Legacy alias for settle_custody."""
    return settle_custody(
        access=access,
        actor=created_by,
        collector=collected_by,
        court=court,
        notes=notes,
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
