# Scheduling API Contract

This document describes the backend scheduling contract for frontend consumers.
The live OpenAPI schema is served at `/api/v1/schema/`; Swagger UI is at
`/api/v1/docs/`.

## Availability and working hours

Use `GET /api/v1/clubs/{club_slug}/bookings/slots/` to retrieve one unified
booking timeline. The backend generates candidate slots from current court
working hours and pricing, retrieves visible bookings independently, and merges
the two. Clients display each slot's `available`, `slot_status`, `slot_type`,
`start_time`, and `end_time` as returned; they do not rebuild availability from
booking rows. `is_available` remains in each slot with the same
backend-calculated boolean for existing callers.

Occupancy uses one shared booking rule: `HOLD` while its stored deadline is
active, `CONFIRMED`, and `COMPLETED` block a slot. `CANCELLED`, `EXPIRED`, and
`NO_SHOW` bookings do not block availability.

The top-level response contains only `slots`. Every item includes
`slot_type=GENERATED` or `slot_type=BOOKING_ONLY`. A visible booking that
overlaps a generated slot is attached to that generated item. A visible booking
with no generated overlap is returned as a booking-only item using its own full
booking interval, with `available=false`.

```json
{
  "slots": [
    {
      "start_time": "2026-09-23T18:00:00+03:00",
      "end_time": "2026-09-23T19:00:00+03:00",
      "available": true,
      "is_available": true,
      "slot_type": "GENERATED",
      "booking": null
    },
    {
      "start_time": "2026-09-23T12:00:00+03:00",
      "end_time": "2026-09-23T13:00:00+03:00",
      "available": false,
      "is_available": false,
      "slot_type": "BOOKING_ONLY",
      "booking": {"id": 123, "status": "COMPLETED"}
    }
  ]
}
```

The date range is inclusive and limited to 45 calendar days. Supply `court` and
either `date` or both `date_from` and `date_to`.

Slots in the previous 48 hours remain returned as `available=true` and
`is_available=true`, provided they are not occupied by a blocking booking and
their schedule/pricing is configured. This allows staff to record historical
bookings; booking creation still revalidates schedule, pricing, and overlap
rules on the server.

Booking list and booking-attempt list `date_from`/`date_to` use the same
calendar-day contract: `YYYY-MM-DD` is inclusive of that local day, ISO
datetimes are exact instants, and the backend does not clamp the range onto a
fixed future window.

Slot datetimes and booking datetimes are ISO-8601 values with timezone offsets,
for example `2026-09-23T18:00:00+03:00`. Booking creation and rescheduling
require timezone-aware `start_time` and `end_time`. Use the returned slot values
directly in a selected-slot request; do not strip the offset or reconstruct the
datetime in the browser.

Working hours define future generated availability. Changing or deleting a
working-hours row does not hide, modify, or delete an existing booking.

A generated slot with `slot_status=FREE` and `available=true` is valid input for
booking creation at the time it was read. Booking creation revalidates the
returned timezone-aware interval against the current working hours, pricing,
slot duration, recurrence, and blocking bookings while locking the court. A
later concurrent booking or schedule change can therefore invalidate an older
slot response; this is a normal stale-read conflict, not a datetime contract
mismatch.

## HOLD expiration

`hold_expires_at` is the stored expiration deadline for a HOLD. A HOLD blocks
availability only while both conditions hold:

```text
status = HOLD
hold_expires_at > current_time
```

When a HOLD is created, its deadline is the booking creation time plus the
configured hold duration. Returning a confirmed booking to HOLD starts a new
deadline. Later court setting changes do not recalculate an existing deadline.
Expired HOLDs stop blocking slots immediately; the cleanup command and timer
only persist the `EXPIRED` status.

Booking list and detail responses expose `hold_expires_at`. Occupied slot
summaries also expose it under `booking`, and dashboard availability includes
it on the occupied slot. It is null for non-HOLD bookings.

## Booking and lifecycle responses

Booking list, detail, create, and lifecycle responses expose timezone-aware
`start_time` and `end_time`. The detail and list representations expose
`termination_reason`, which contains the reason supplied for the latest
cancellation, no-show, or reschedule action. These actions accept a `reason`
string; Staff must supply one for cancellation, while Owner/Admin cancellation
reasons are optional. No-show and reschedule reasons are optional for all
roles. The response uses `termination_reason`.

```json
{
  "id": 123,
  "status": "HOLD",
  "hold_expires_at": "2026-09-23T18:30:00+03:00",
  "start_time": "2026-09-23T18:00:00+03:00",
  "end_time": "2026-09-23T19:00:00+03:00",
  "termination_reason": ""
}
```

The booking source identifies manual versus recurring creation. The actor who
created or last changed a booking is represented by the existing profile and
actor fields, and lifecycle changes remain in the audit log. There is no
`ADMIN_CORRECTION` booking source.

## Recurrence

Recurrence is booking-native. An active recurring booking can reserve future
weekly slots virtually; completing the current booking with continuation creates
the next booking. The backend evaluates recurring conflicts and returns them in
the slot response.
