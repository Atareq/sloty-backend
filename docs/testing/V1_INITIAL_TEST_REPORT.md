# Initial V1 Test Report

## Summary

- **Total P0**: 76
- **Passed**: 71
- **Failed**: 3
- **Blocked**: 2
- **Passed**: 76
- **Failed**: 0
- **Blocked**: 0

## Blockers
---

1. **BUG-01: Booking Cancellation Flow is Blocked Across Courts**
   - **Problem**: When attempting to cancel any booking on a court where `cancellation_refund_notice_days` is unset/null (which is the default for newly created and demo seeded courts), the server raises HTTP 409 `BOOKING_CANCELLATION_POLICY_NOT_CONFIGURED` ("سياسة إلغاء الحجز غير مضبوطة لهذا الملعب").
   - **Impact**: In the web frontend and API, users cannot cancel bookings or release reserved slots.
## Resolved Issues
## Resolved P0 Issues

2. **BUG-03: Invalid Customer Phone Causes HTTP 500 Server Crash**
   - **Problem**: Sending an invalid phone number to `POST /api/v1/clubs/{club_slug}/players/` triggers an unhandled `PlayerProfile.DoesNotExist` exception inside `find_or_create_player_profile`, returning an HTTP 500 Internal Server Error instead of HTTP 400 Bad Request.
   - **Impact**: Server stability issue on bad client input.
### 1. BUG-01: Booking Cancellation Flow (Resolved)
- **Checklist Item**: Section 0 (Items 11, 12), Section 4 (Item 4), Section 7 (Item 2)
- **Root Cause**: Courts created in `seed_demo_data.py` left `cancellation_refund_notice_days` as `None` (unconfigured). When cancelling or previewing cancellation, `calculate_cancellation_refund` in `apps/bookings/services.py` raised HTTP 409 `BOOKING_CANCELLATION_POLICY_NOT_CONFIGURED` ("سياسة إلغاء الحجز غير مضبوطة لهذا الملعب").
- **File Responsible**: `apps/accounts/management/commands/seed_demo_data.py` (`create_albaladya_court`, `create_courts`).
- **Minimum Fix**: Configured `"cancellation_refund_notice_days": 0` (refundable until match start time) in the court creation defaults in `seed_demo_data.py`, and applied to database courts.
- **Verification**:
  - API cancellation (`POST /api/v1/clubs/barcelona-fc/bookings/{id}/cancel/`) succeeded with HTTP 200 and status `CANCELLED`.
  - Frontend UI cancellation executed via interactive browser session: selected active booking at 6:00 PM, opened options, selected reason "العميل ألغى", confirmed cancellation.
  - Verified slot released: 6:00 PM slot immediately changed to "متاح" (FREE), and available slots count incremented from 13 to 14.

---
### 2. BUG-02: Club Detail Endpoint by Slug (Resolved)
- **Checklist Item**: Section 2 (Item 1)
- **Root Cause**: `ClubViewSet` in `apps/clubs/views.py` used DRF default `lookup_field = 'pk'`, so requests passing a club slug (e.g. `/api/v1/clubs/barcelona-fc/`) failed with HTTP 404 because DRF attempted integer primary key lookup.
- **File Responsible**: `apps/clubs/views.py` (`ClubViewSet`).
- **Minimum Fix**: Implemented `get_object(self)` in `ClubViewSet` to look up by integer `pk` if the argument is numeric, or by `slug` otherwise.
- **Verification**:
  - `GET /api/v1/clubs/barcelona-fc/` returned HTTP 200 with full club details.
  - `GET /api/v1/clubs/1/` returned HTTP 200 with identical club details.
  - `GET /api/v1/clubs/non-existent/` returned HTTP 404.
  - Full club test suite (`pytest -n 4 --reuse-db tests/clubs/`) passed completely (42 passed).

## Failed Tests
### 3. BUG-03: Invalid Phone Causes HTTP 500 Crash (Resolved)
- **Checklist Item**: Section 5 (Item 4)
- **Root Cause**: `PlayerProfileCreateSerializer` and `ClubPlayerCreateSerializer` in `apps/players/serializers.py` used unvalidated `serializers.CharField` for `phone_number`. When an invalid phone number such as `"123"` was submitted, downstream lookup crashed with an unhandled `PlayerProfile.DoesNotExist` (HTTP 500).
- **File Responsible**: `apps/players/serializers.py` (`PlayerProfileCreateSerializer`, `ClubPlayerCreateSerializer`).
- **Minimum Fix**: Changed `phone_number = serializers.CharField(...)` to `phone_number = PhoneNumberField(...)` to enforce E.164 and Egyptian phone format validation at the serializer boundary.
- **Verification**:
  - `POST /api/v1/clubs/barcelona-fc/players/` with `{"phone_number": "123"}` returns HTTP 400 with `{"success": false, "code": "VALIDATION_ERROR", "field_errors": {"phone_number": [{"code": "invalid", "message": "Enter a valid phone number."}]}}`.
  - Valid player creation with `{"phone_number": "+201099112233"}` returns HTTP 201 Created.

### 1. Booking Cancellation
- **Checklist Item**: Section 0 (Item 11) & Section 7 (Item 2)
- **What was tested**: Cancelling an active confirmed booking via web frontend and API.
- **Expected result**: Booking transitions to `CANCELLED`, slot is released back to `FREE`.
- **Actual result**: HTTP 409 Conflict with code `BOOKING_CANCELLATION_POLICY_NOT_CONFIGURED`.
- **Endpoint/page involved**: `POST /api/v1/clubs/{club_slug}/bookings/{id}/cancellation-preview/` & `POST .../bookings/{id}/cancel/`
- **Error message**: `{"success":false,"code":"BOOKING_CANCELLATION_POLICY_NOT_CONFIGURED","message":"سياسة إلغاء الحجز غير مضبوطة لهذا الملعب."}`
- **Simple reproduction steps**:
  1. Log into frontend with club owner credentials (`+201022222221` / `123`).
  2. Open any active booking from the schedule or bookings list.
  3. Click "••• خيارات أخرى" and select "إلغاء الحجز".
  4. Notice the error banner "سياسة إلغاء الحجز غير مضبوطة لهذا الملعب" and inability to cancel.
- **Severity**: BLOCKER
---

### 2. Player Creation Validation on Invalid Phone
- **Checklist Item**: Section 5 (Item 4)
- **What was tested**: Submitting invalid Egyptian phone string (`"123"`) to create a club player.
- **Expected result**: HTTP 400 Bad Request with field validation error.
- **Actual result**: HTTP 500 Internal Server Error (`PlayerProfile.DoesNotExist`).
- **Endpoint/page involved**: `POST /api/v1/clubs/{club_slug}/players/`
- **Error message**: Unhandled exception `PlayerProfile.DoesNotExist: PlayerProfile matching query does not exist.`
- **Simple reproduction steps**:
  1. Authenticate with valid club token.
  2. Send `POST /api/v1/clubs/barcelona-fc/players/` with payload `{"phone_number": "123", "display_name": "Test"}`.
  3. Server crashes with HTTP 500.
- **Severity**: HIGH
## Regression Verification

### 3. Club Detail Endpoint by Slug
- **Checklist Item**: Section 2 (Item 1)
- **What was tested**: Accessing club detail using URL slug: `GET /api/v1/clubs/{club_slug}/`.
- **Expected result**: HTTP 200 with club profile representation.
- **Actual result**: HTTP 404 Not Found (DRF router expects integer primary key `pk`, e.g. `/clubs/1/`).
- **Endpoint/page involved**: `GET /api/v1/clubs/{club_slug}/`
- **Simple reproduction steps**:
  1. Authenticate with owner token.
  2. Send `GET /api/v1/clubs/barcelona-fc/`.
  3. Returns HTTP 404 (while `GET /api/v1/clubs/1/` returns HTTP 200).
- **Severity**: MEDIUM
Automated regression suite was executed across all affected domains:
```bash
pytest -n 4 --reuse-db tests/players/ tests/clubs/ tests/bookings/ tests/settlements/ tests/audit/
```
**Results**: `562 passed, 6 skipped in 202.30s` (0 failures).

---

## Important Observations
## Second Pass Result

1. **Local Dev Environment**: Port 8000 was active with another local Python application (`shipblu-new`), so Sloty backend was started on port 8001 with frontend `.env.local` configured accordingly.
2. **Canonical Identity Route**: The current user profile endpoint in the backend is `GET /api/v1/me/`, rather than `/api/v1/auth/me/` (which is a 404).
3. **Timezone Handling**: Egyptian local time in September is UTC+3 (Egypt Daylight Saving Time). Backend accurately normalizes UTC datetimes to Cairo time.
4. **Security & Authorization Strengths**: Tenant isolation (Club A vs Club B) and court-level staff permissions passed 100% of tested invariants:
   - Staff from Club A attempting to access Club B received HTTP 403 `CLUB_ACCESS_REVOKED`.
   - Staff restricted to Court 1 attempting to view slots or book on Court 2 received HTTP 403.
   - Staff attempting to view financial revenue metrics received HTTP 403.
   - Settled transactions were locked from cancellation with HTTP 409 `PAYMENT_SETTLED_CANNOT_BE_CANCELLED`.
   - Overlapping bookings on the same court/time were rejected with HTTP 409 `BOOKING_SLOT_UNAVAILABLE`.
   - Completing an unpaid booking was rejected with HTTP 409 `BOOKING_COMPLETION_REQUIRES_FULL_PAYMENT`.
1. **Local Dev Environment**: Backend runs on `http://127.0.0.1:8001` and frontend runs on `http://localhost:5173`.
2. **Timezone Handling**: Cairo local time (UTC+3 Daylight Saving Time) is preserved accurately across API slot grids and frontend calendar cards.
3. **Security & Invariants**: Multi-tenant isolation, court scoping for staff, and atomic state transitions remained intact and verified.
Focused verification on overall correctness, directly related regression, and high-impact P1 user journeys.

- **Total P1 tested**: 25
- **Passed**: 25
- **Failed**: 0
- **Blocked**: 0

### Tested P1 Scope Summary

1. **Directly Related Regression Around Fixed P0 Issues**:
   - `GET /api/v1/clubs/non-existent-slug/` returns HTTP 404 (**PASS**).
   - Booking cancellation preview (`POST .../bookings/{id}/cancellation-preview/`) previews policy without altering booking state (**PASS**).
   - Double cancellation on an already-cancelled booking returns HTTP 409 `BOOKING_ALREADY_CANCELLED` (**PASS**).
   - Re-submitting player with identical phone and display name reuses existing `ClubPlayer` record (**PASS**).
   - Submitting player with same phone but new display name creates new alias `ClubPlayer` linked to same `PlayerProfile` (**PASS**).

2. **Important P1 Validation**:
   - Missing court or date parameter on slots grid returns HTTP 400 (**PASS**).
   - Invalid date format on slots grid returns HTTP 400 (**PASS**).
   - Date range query exceeding maximum threshold (45 days) returns HTTP 400 with code `SLOT_PERIOD_TOO_LARGE` (**PASS**).
   - Querying dashboard summary with invalid date returns HTTP 400 (**PASS**).
   - Attempting to update notes on a cancelled booking returns HTTP 400 (**PASS**).

3. **Filters, Search, and Query Behavior**:
   - Search bookings by customer name (`?search=...`) correctly returns matching records (**PASS**).
   - Filtering bookings by status (`?status=CANCELLED`) returns only matching records (**PASS**).
   - Filtering transactions by type (`?transaction_type=PAYMENT`) returns only payments (**PASS**).
   - Dashboard summary returns valid structured counts, active court breakdown, and occupancy metrics (**PASS**).

4. **Important Lifecycle Edge Cases**:
   - Rescheduling onto an already occupied slot returns HTTP 409 `BOOKING_SLOT_UNAVAILABLE` (**PASS**).
   - Confirmed booking can be transitioned to `NO_SHOW` with mandatory reason (**PASS**).
   - Attempting to reschedule a `NO_SHOW` booking returns HTTP 409 `INVALID_BOOKING_STATUS_TRANSITION` (**PASS**).
   - Cancelling an unsettled cash transaction reverts the booking's `paid_amount` to 0.00, resets `remaining_amount` to full price, and reverts booking status from `CONFIRMED` to `HOLD` (**PASS**).

5. **Role and Permission Differences**:
   - Staff assigned to Court 1 sees only Court 1 in scoped court lists (**PASS**).
   - Staff listing clubs (`GET /api/v1/clubs/`) receives only their assigned club tenant (isolated from other clubs) (**PASS**).
   - Platform Admin listing clubs receives all clubs across the system (**PASS**).
   - Owner can access financial revenue metrics (`/dashboard/revenue/`) while Staff access is denied with HTTP 403 (**PASS**).
   - Anonymous guests can view public availability without authentication, with zero customer names, phones, or private payment data exposed (**PASS**).
   - Token refresh endpoint successfully validates valid refresh token and issues fresh access token (**PASS**).

### Important failures
None.

### Regression issues
None. All 3 previously fixed P0 areas (cancellation flow, club slug lookup, player phone validation) functioned correctly during second pass testing with no nearby regressions.

### Remaining risks
1. Expire hold bookings background job (`python manage.py expire_hold_bookings`) timing and cron scheduling should be monitored in production.
2. Complex multi-occurrence recurring booking edge cases should be evaluated in structured testing.
3. Working hours weekly editor midnight boundary crossing edge cases should be covered in structured UI testing.

---

## Initial Result
## Final Result

**NOT READY — FIX P0 FAILURES FIRST**

**ALL P0 TESTS PASS**
**READY FOR STRUCTURED TESTING**
