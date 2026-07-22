from datetime import time

from apps.bookings.models import Booking

REPORT_MAX_RANGE_DAYS = 31
EVENING_START_TIME = time(18, 0)
DEMAND_BUCKET_MINUTES = 60

PERIOD_ALL_DAY = "all_day"
PERIOD_DAYTIME = "daytime"
PERIOD_EVENING = "evening"
PERIOD_CUSTOM = "custom"
PERIOD_CHOICES = (
    PERIOD_ALL_DAY,
    PERIOD_DAYTIME,
    PERIOD_EVENING,
    PERIOD_CUSTOM,
)

DEFAULT_USAGE_STATUSES = (
    Booking.Status.CONFIRMED,
    Booking.Status.COMPLETED,
    Booking.Status.NO_SHOW,
)
ALLOWED_USAGE_STATUSES = (
    Booking.Status.HOLD,
    Booking.Status.CONFIRMED,
    Booking.Status.COMPLETED,
    Booking.Status.NO_SHOW,
)
