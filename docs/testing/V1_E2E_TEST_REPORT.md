# V1 E2E Test Report

**Execution Date**: September 21, 2026
**Environment**: Local Live Stack
- Backend API: `http://127.0.0.1:8001` (Django 5.x / DRF modular monolith on PostgreSQL)
- Frontend Client: `http://localhost:5173` (React 19 + TypeScript + Vite)
- Test Runner: Playwright (`@playwright/test`) running real Google Chrome

---

## Existing Coverage Found

- **Previous State**: Prior to this testing phase, the repository had **zero automated browser-level E2E tests**. Existing test coverage was strictly component-level and unit-level:
  - Backend: `pytest` suites covering services, models, authorization spine, and serializers (`tests/`).
  - Frontend: `vitest` suites testing UI components in isolation with `@testing-library/react` and mocked Dexie storage.
- **What Was Missing**:
  - Verification of real browser navigation, JWT storage in `localStorage`, and HTTP header propagation.
  - End-to-end integration between frontend modal flows (`AppSheet`, `AddBookingSheet`, `RecordPaymentSheet`, `RescheduleBookingSheet`, `CancelBookingReasonSheet`) and backend atomic database transactions.
  - Live verification that client-side role guards correctly prevent unauthorized navigation in a running browser.
  - Live validation error feedback and graceful UI handling when invalid input is submitted.

---

## E2E Tests Added

We introduced a lightweight, focused Playwright E2E suite under `e2e/` targeting the 6 critical user flows without visual regression bloat or UI overengineering:

### 1. Authentication (`e2e/auth.spec.ts`)
- **`valid login reaches schedule and stores session`**: Submitting valid owner credentials (`01022222221` / `123`) authenticates against `/api/v1/auth/token/`, persists `sloty_access_token` and `sloty_refresh_token` in `localStorage`, and navigates to `/schedule`.
- **`invalid login shows Arabic error and stays on /login`**: Submitting wrong credentials shows localized error message (`بيانات الدخول غير صحيحة...`) and keeps the user on the login screen.
- **`unauthenticated access to protected route redirects to /login`**: Directly navigating to `/schedule` without authentication is intercepted by `ProtectedRoute` and redirected to `/login`.
- **`logout through menu confirmation sheet redirects to /login and clears session`**: Clicking "تسجيل الخروج" opens the confirmation sheet warning about unsaved data; confirming clears tokens and redirects to `/login`.

### 2. Club / Court Bootstrap & Permissions (`e2e/bootstrap_and_permissions.spec.ts`)
- **`owner bootstrap: loads club and all courts`**: Logs in as Owner (`01022222221`), verifies club `Barcelona FC` loads, court selector renders both `Spotify Camp Nou` and `La Masia Training Court`, and morning/evening schedule slots render.
- **`staff bootstrap: loads single assigned court without multi-court picker`**: Logs in as Staff (`01033333331`), verifies staff only sees assigned court `Spotify Camp Nou` (multi-court dropdown is omitted since staff has 1 court).
- **`staff permission boundary enforcement`**: Direct navigation by Staff to owner-only administration pages (`/settings/courts` and `/audit-logs`) is intercepted by `RoleRoute` and redirects the user safely back to `/schedule`.

### 3. Booking Core Journey (`e2e/booking_journey.spec.ts`)
- **`complete booking lifecycle: create -> verify -> pay deposit -> reschedule -> cancel -> verify slot released`**:
  1. Chooses an available slot on the schedule (`8:00 م`).
  2. Opens `AddBookingSheet` ("حجز جديد"), enters customer phone (`01055554444`) and name (`كابتن محمد`), and confirms.
  3. Verifies new booking appears on schedule with status `HOLD` ("بانتظار العربون").
  4. Opens booking details dialog ("تفاصيل الحجز").
  5. Clicks "سجّل العربون", opens `RecordPaymentSheet`, enters deposit amount (`300`), and submits payment.
  6. Verifies status updates immediately to `CONFIRMED` ("العربون مدفوع").
  7. Opens booking options ("خيارات أخرى") -> "تغيير الموعد", opens `RescheduleBookingSheet`, selects new slot (`9:00 م`), and confirms reschedule.
  8. Verifies booking details dialog updates with new slot (`٩:٠٠ م - ١٠:٠٠ م`) and status "العربون مدفوع".
  9. Opens "خيارات أخرى" -> "إلغاء الحجز", selects reason "العميل ألغى", and confirms cancellation.
  10. Verifies both slots (`8:00 م` and `9:00 م`) are completely released and marked "متاح" on the schedule.

### 4. Validation & Error Handling (`e2e/validation.spec.ts`)
- **`AddBookingSheet validates required fields and phone format`**:
  - Missing name displays: `"اسم العميل مطلوب"`.
  - Missing phone displays: `"رقم الموبايل مطلوب"`.
  - Invalid phone format (e.g. `123`) displays: `"رقم الموبايل غير صحيح"`.
  - Form remains stable and responsive without crashes or unhandled exceptions.
- **`RecordPaymentSheet validates missing or invalid amount`**:
  - Submitting without amount displays: `"المبلغ مطلوب"`.
  - Submitting an amount below minimum deposit displays minimum deposit constraint.
  - Form remains interactive and can be dismissed safely.

### 5. Non-Operational User Flow (`e2e/non_operational.spec.ts`)
- **`PLAYER account redirects to /no-operational-access`**:
  - Logs in as a customer/player user (`01099000001`).
  - Redirected immediately to `/no-operational-access` with clear Arabic explanation ("حسابك مسجل كلاعب وليس لديك صلاحية تشغيلية...").
  - Verifies zero requests are sent to operational endpoints (`/api/v1/clubs/`, `/api/v1/courts/`).
  - Authentication tokens remain intact in `localStorage`.
  - User can log out cleanly.
- **`null role user lands on /no-operational-access safely`**:
  - Logs in as account with `profile_role = null` (`01099000002`).
  - Handled safely and routed to `/no-operational-access` without infinite redirects or console errors.

---

## Bugs Discovered

- **No critical E2E bugs were found.** All core flows, permission guards, state transitions, and validation handlers functioned smoothly across the live frontend and backend stack.
- **Domain Invariant Verified**: During testing of the reschedule flow, verified that `RescheduleBookingSheet` strictly enforces `!isPastSlot(selectedDate, slot.endTime)` using Egypt local time, correctly preventing users from rescheduling bookings into hours that have already passed earlier in the day.

---

## Results

| Test File | Test Description | Status | Duration |
| :--- | :--- | :--- | :--- |
| `e2e/auth.spec.ts` | Valid login reaches schedule & stores session | **PASS** | 2.0s |
| `e2e/auth.spec.ts` | Invalid login shows Arabic error | **PASS** | 1.4s |
| `e2e/auth.spec.ts` | Unauthenticated access redirects to `/login` | **PASS** | 0.5s |
| `e2e/auth.spec.ts` | Logout via confirmation sheet clears session | **PASS** | 2.4s |
| `e2e/bootstrap_and_permissions.spec.ts` | Owner bootstrap: loads club and all courts | **PASS** | 2.5s |
| `e2e/bootstrap_and_permissions.spec.ts` | Staff bootstrap & permission route guards | **PASS** | 3.7s |
| `e2e/booking_journey.spec.ts` | Full lifecycle: create -> pay -> reschedule -> cancel -> release | **PASS** | 4.6s |
| `e2e/validation.spec.ts` | AddBookingSheet required fields & phone format | **PASS** | 2.4s |
| `e2e/validation.spec.ts` | RecordPaymentSheet missing & invalid amount | **PASS** | 2.8s |
| `e2e/non_operational.spec.ts` | PLAYER account lands on `/no-operational-access` without bootstrap | **PASS** | 1.7s |
| `e2e/non_operational.spec.ts` | Null role user lands on `/no-operational-access` safely | **PASS** | 1.3s |

- **Total Tests**: 11
- **Passed**: 11
- **Failed**: 0
- **Blocked**: 0
- **Suite Execution Time**: 26.3s
- **Unit Test Regression (`vitest`)**: 158/158 files passed (1,252/1,252 tests passed in 61.5s)

---

## Remaining Gaps

The following non-critical areas were intentionally not automated in this initial E2E phase per the "Simplicity Wins" mandate:
- **Visual styling and layout screenshot tests**: Purposely excluded to keep tests resilient across viewports.
- **Concurrent multi-session slot contention**: Race conditions between two simultaneous browsers booking the exact same second are covered by backend PostgreSQL transaction lock tests (`tests/test_postgresql_concurrency.py`).
- **Offline / PWA background sync simulation**: Offline Dexie queues and reconnect sync are validated via dedicated Vitest integration suites (`src/offline/sync/`).
- **Deep analytical report charts & PDF exports**: Covered by report service and viewset unit tests.

---

## Result

**CRITICAL E2E FLOWS PASSED**
