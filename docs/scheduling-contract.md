# Scheduling API Contract

This document describes the backend scheduling contract for frontend consumers.
The live OpenAPI schema is served at `/api/v1/schema/`; Swagger UI is at
`/api/v1/docs/`.

## Availability and working hours

Use `GET /api/v1/clubs/{club_slug}/bookings/slots/` to retrieve generated
booking slots. The backend applies current court working hours, pricing,
existing bookings, active recurring reservations, and HOLD deadlines. Clients
display each slot's `available`, `slot_status`, `start_time`, and `end_time` as
returned; they do not rebuild availability from booking rows. `is_available`
remains in the response with the same backend-calculated boolean for existing
callers.

Occupancy uses one shared booking rule: `HOLD` while its stored deadline is
active, `CONFIRMED`, and `COMPLETED` block a slot. `CANCELLED`, `EXPIRED`, and
`NO_SHOW` bookings do not block availability.

```json
{
  "slots": [
    {
      "start_time": "2026-09-23T18:00:00+03:00",
      "end_time": "2026-09-23T19:00:00+03:00",
      "available": true,
      "is_available": true
    }
  ]
}
```

The date range is inclusive and limited to 45 calendar days. Supply `court` and
either `date` or both `date_from` and `date_to`.

On the current local Cairo day, slots whose `start_time` is already in the
past are returned with `available=false` and `is_available=false`. Clients
must not treat those hours as bookable. Requested past calendar days still
return FREE slots so staff can create historical bookings.

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
