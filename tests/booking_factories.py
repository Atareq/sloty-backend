"""Persist test bookings with required ClubPlayer identity."""

from decimal import Decimal

from apps.bookings.models import Booking
from apps.bookings.services import resolve_booking_club_player


def persist_booking(court, **extra_fields) -> Booking:
    """Create a Booking row through the ORM with a resolved ClubPlayer.

    ``customer_name`` / ``customer_phone`` are resolution inputs only. They
    are never stored on Booking.
    """
    start_time = extra_fields.pop("start_time")
    end_time = extra_fields.pop("end_time")
    customer_name = extra_fields.pop("customer_name", "Test Customer")
    customer_phone = extra_fields.pop("customer_phone", "+201000000001")
    club_player = extra_fields.pop("club_player", None)
    extra_fields.pop("club", None)
    if club_player is None:
        club_player = resolve_booking_club_player(
            club=court.club,
            customer_name=customer_name,
            customer_phone=customer_phone,
        )
    data = {
        "club": court.club,
        "court": court,
        "club_player": club_player,
        "start_time": start_time,
        "end_time": end_time,
        "total_price": Decimal("300.00"),
        "status": Booking.Status.HOLD,
        "source": Booking.Source.MANUAL,
    }
    data.update(extra_fields)
    return Booking.objects.create(**data)
