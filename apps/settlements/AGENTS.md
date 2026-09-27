# Settlements — Agent Guide

## Responsibility

`apps/settlements/` owns settlement records, current-custody previews, and grouped unsettled-custody summaries.

## Identity and custody

- `Transaction.created_by` is the collector Profile and the source of financial custody.
- `Settlement.settled_by` is the Profile that performed the settlement. It is set from the request's validated `context.profile` and is not client-controlled.
- API actor and custody IDs are Profile IDs. `collected_by` never accepts or returns a User ID alias.
- Collector display names may come from `Profile.user`; display metadata does not change Profile identity.
- Keep `collected_by` and `settled_by` distinct. One identifies whose transaction custody is being settled; the other identifies who performed settlement.

## Domain rules

- Unsettled custody consists of transactions in the selected club where `is_cancelled=false` and `settlement_line IS NULL`.
- The predicate above is implemented only by `apps.transactions.services.settlement_eligible_transactions()`; settlement calculation applies optional collector filtering and locking after consuming that queryset.
- Custody includes signed refunds and is not limited by transaction age, booking status, or payment method.
- Preview, unsettled summary, and settlement creation use the same club-wide calculation across courts. Their optional `court` input is validated and treated as metadata; it never narrows custody candidates.
- The calculation supports an optional collector Profile. With one, custody is limited to `Transaction.created_by=collector`; without one, it includes every eligible transaction in the club.
- Preview returns the calculation without mutation. Creation runs that calculation with row locking inside `transaction.atomic()` and links each candidate once through `SettlementTransaction.transaction`.
- A settlement is created with its final `settled_by` actor; the current model has no settlement status or pending/mark-settled lifecycle.
- `Settlement` is append-only (`SlotyCreatedModel`). The insert timestamp is `created` (not `created_at`).
- Transaction persistence time (`Transaction.created`) determines candidate ordering and derived settlement periods. `period_start` and `period_end` are the earliest and latest eligible persistence times; `occurred_at` does not replace them.
- Authorization remains in the Authorization Spine and `apps/settlements/authorization.py`. Preserve club scope and collector authority checks.

## API and OpenAPI contract

- Club-scoped routes live under `/api/v1/clubs/{club_slug}/settlements/`.
- `GET .../preview/?collected_by=<profile_id>` previews one collector. If omitted, the validated request Profile is used.
- `GET .../unsettled-summary/?collected_by=<profile_id>` optionally narrows the management summary to one collector.
- Settlement list filters `settled_by` by the performing Profile ID and `collected_by` by `Transaction.created_by` Profile ID.
- Settlement response `settled_by` and custody response `collected_by` values are Profile IDs. Display-name fields are presentation only.
- Document Profile-ID semantics in serializers and filters. Do not introduce User-ID fallbacks or alternate actor identifiers.

## Authorization boundary

- `Settlement.authorization_config` declares club scope. Do not treat court as a required settlement authorization scope.
- Preserve current role and club visibility rules in `apps/settlements/authorization.py` and the shared Authorization Spine.
- Pure settlement services receive domain objects such as `club`, `collected_by` Profile, and `settled_by` Profile. They do not resolve request identity or permissions.

## Cross-app dependencies

- Queries and locks `apps/transactions` rows.
- Booking customer presentation comes from `Booking.club_player` through `apps.bookings.identity`.
- `apps/clubs/services.py` uses unsettled custody when managing staff lifecycle; `apps/dashboard/services.py` uses it for financial metrics.

## Testing

- Settlement tests live under `tests/settlements/`.
- Cover preview/creation parity, signed custody, Profile-ID filters, actor versus collector identity, authorization, and cross-club isolation.
- Run the project-standard parallel suite for the affected domain; see root `AGENTS.md` §8.
