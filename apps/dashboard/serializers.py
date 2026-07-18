from datetime import datetime, time, timedelta

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from apps.bookings.models import Booking
from apps.courts.models import Court
from apps.transactions.models import Transaction


def make_aware_if_needed(value):
    if timezone.is_naive(value):
        return timezone.make_aware(value, timezone.get_current_timezone())
    return value


def day_bounds(date_value):
    start = datetime.combine(date_value, time.min)
    end = start + timedelta(days=1)
    return (
        timezone.make_aware(start, timezone.get_current_timezone()),
        timezone.make_aware(end, timezone.get_current_timezone()),
    )


def parse_query_datetime(value, *, field_name, date_is_end=False):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return make_aware_if_needed(value)

    parsed_datetime = parse_datetime(str(value))
    if parsed_datetime is not None:
        return make_aware_if_needed(parsed_datetime)

    parsed_date = parse_date(str(value))
    if parsed_date is not None:
        start, end = day_bounds(parsed_date)
        return end if date_is_end else start

    raise serializers.ValidationError(
        {field_name: "Enter a valid ISO datetime or YYYY-MM-DD date."}
    )


def today_bounds():
    return day_bounds(timezone.localdate())


def month_start():
    today = timezone.localdate()
    return timezone.make_aware(
        datetime.combine(today.replace(day=1), time.min),
        timezone.get_current_timezone(),
    )


class AccessScopedCourtMixin:
    def validate_court_access(self, court):
        access = self.context["club_access"]
        if court is None:
            return None
        if not access.can_access_court(court):
            raise PermissionDenied("You cannot access this court.")
        return court


class AvailabilityQuerySerializer(serializers.Serializer):
    date = serializers.DateField(required=True)


class CalendarQuerySerializer(AccessScopedCourtMixin, serializers.Serializer):
    date = serializers.DateField(required=False)
    date_from = serializers.CharField(required=False, allow_blank=True)
    date_to = serializers.CharField(required=False, allow_blank=True)
    court = serializers.PrimaryKeyRelatedField(
        queryset=Court.objects.all(),
        required=False,
        allow_null=True,
    )
    status = serializers.ChoiceField(
        choices=Booking.Status.choices,
        required=False,
        allow_blank=True,
    )

    def validate(self, attrs):
        if attrs.get("date"):
            attrs["date_from"], attrs["date_to"] = day_bounds(attrs["date"])
        elif not attrs.get("date_from") and not attrs.get("date_to"):
            attrs["date_from"], attrs["date_to"] = today_bounds()
        else:
            if not attrs.get("date_from") or not attrs.get("date_to"):
                raise serializers.ValidationError(
                    "date_from and date_to must be supplied together."
                )
            attrs["date_from"] = parse_query_datetime(
                attrs["date_from"],
                field_name="date_from",
            )
            attrs["date_to"] = parse_query_datetime(
                attrs["date_to"],
                field_name="date_to",
                date_is_end=True,
            )

        if attrs["date_from"] >= attrs["date_to"]:
            raise serializers.ValidationError(
                {"date_to": "date_to must be after date_from."}
            )
        attrs["court"] = self.validate_court_access(attrs.get("court"))
        if attrs.get("status") == "":
            attrs["status"] = None
        return attrs


class DashboardOverviewQuerySerializer(AccessScopedCourtMixin, serializers.Serializer):
    date_from = serializers.CharField(required=False, allow_blank=True)
    date_to = serializers.CharField(required=False, allow_blank=True)
    court = serializers.PrimaryKeyRelatedField(
        queryset=Court.objects.all(),
        required=False,
        allow_null=True,
    )

    def validate(self, attrs):
        default_start, default_end = today_bounds()
        attrs["date_from"] = (
            parse_query_datetime(
                attrs.get("date_from"),
                field_name="date_from",
            )
            or default_start
        )
        attrs["date_to"] = (
            parse_query_datetime(
                attrs.get("date_to"),
                field_name="date_to",
                date_is_end=True,
            )
            or default_end
        )
        if attrs["date_from"] >= attrs["date_to"]:
            raise serializers.ValidationError(
                {"date_to": "date_to must be after date_from."}
            )
        attrs["court"] = self.validate_court_access(attrs.get("court"))
        return attrs


class DashboardSummaryQuerySerializer(AccessScopedCourtMixin, serializers.Serializer):
    date = serializers.DateField(required=False)
    date_from = serializers.CharField(required=False, allow_blank=True)
    date_to = serializers.CharField(required=False, allow_blank=True)
    court = serializers.PrimaryKeyRelatedField(
        queryset=Court.objects.all(),
        required=False,
        allow_null=True,
    )

    def validate(self, attrs):
        has_date = bool(attrs.get("date"))
        has_date_from = bool(attrs.get("date_from"))
        has_date_to = bool(attrs.get("date_to"))

        if has_date and (has_date_from or has_date_to):
            raise serializers.ValidationError(
                "date cannot be combined with date_from or date_to."
            )

        if has_date:
            attrs["date_from"], attrs["date_to"] = day_bounds(attrs["date"])
        elif has_date_from or has_date_to:
            if not has_date_from or not has_date_to:
                raise serializers.ValidationError(
                    "date_from and date_to must be supplied together."
                )
            attrs["date_from"] = parse_query_datetime(
                attrs["date_from"],
                field_name="date_from",
            )
            attrs["date_to"] = parse_query_datetime(
                attrs["date_to"],
                field_name="date_to",
                date_is_end=True,
            )
        else:
            attrs["date_from"], attrs["date_to"] = today_bounds()

        if attrs["date_from"] >= attrs["date_to"]:
            raise serializers.ValidationError(
                {"date_to": "date_to must be after date_from."}
            )
        attrs["court"] = self.validate_court_access(attrs.get("court"))
        return attrs


class RevenueQuerySerializer(AccessScopedCourtMixin, serializers.Serializer):
    date_from = serializers.CharField(required=False, allow_blank=True)
    date_to = serializers.CharField(required=False, allow_blank=True)
    group_by = serializers.ChoiceField(
        choices=("day", "week", "month"),
        required=False,
        default="day",
    )
    court = serializers.PrimaryKeyRelatedField(
        queryset=Court.objects.all(),
        required=False,
        allow_null=True,
    )
    payment_method = serializers.ChoiceField(
        choices=Transaction.PaymentMethod.choices,
        required=False,
        allow_blank=True,
    )

    def validate(self, attrs):
        _, default_end = today_bounds()
        attrs["date_from"] = (
            parse_query_datetime(
                attrs.get("date_from"),
                field_name="date_from",
            )
            or month_start()
        )
        attrs["date_to"] = (
            parse_query_datetime(
                attrs.get("date_to"),
                field_name="date_to",
                date_is_end=True,
            )
            or default_end
        )
        if attrs["date_from"] >= attrs["date_to"]:
            raise serializers.ValidationError(
                {"date_to": "date_to must be after date_from."}
            )
        attrs["court"] = self.validate_court_access(attrs.get("court"))
        if attrs.get("payment_method") == "":
            attrs["payment_method"] = None
        return attrs


class CourtUtilizationQuerySerializer(serializers.Serializer):
    date_from = serializers.CharField(required=False, allow_blank=True)
    date_to = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        default_start, default_end = today_bounds()
        attrs["date_from"] = (
            parse_query_datetime(
                attrs.get("date_from"),
                field_name="date_from",
            )
            or default_start
        )
        attrs["date_to"] = (
            parse_query_datetime(
                attrs.get("date_to"),
                field_name="date_to",
                date_is_end=True,
            )
            or default_end
        )
        if attrs["date_from"] >= attrs["date_to"]:
            raise serializers.ValidationError(
                {"date_to": "date_to must be after date_from."}
            )
        return attrs


class AvailabilityClubSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    slug = serializers.CharField()
    name = serializers.CharField()


class AvailabilityCourtSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class AvailabilitySlotSerializer(serializers.Serializer):
    start_time = serializers.DateTimeField()
    end_time = serializers.DateTimeField()
    is_available = serializers.BooleanField()
    blocking_booking = serializers.IntegerField(allow_null=True)
    blocking_status = serializers.CharField(allow_null=True)


class AvailabilityResponseSerializer(serializers.Serializer):
    club = AvailabilityClubSerializer()
    court = AvailabilityCourtSerializer()
    date = serializers.DateField()
    is_closed = serializers.BooleanField()
    opens_at = serializers.TimeField(allow_null=True)
    closes_at = serializers.TimeField(allow_null=True)
    slot_duration_minutes = serializers.IntegerField()
    slots = AvailabilitySlotSerializer(many=True)


class CalendarItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    court = serializers.IntegerField()
    court_name = serializers.CharField()
    title = serializers.CharField()
    customer_name = serializers.CharField()
    customer_phone = serializers.CharField()
    start_time = serializers.DateTimeField()
    end_time = serializers.DateTimeField()
    status = serializers.CharField()
    source = serializers.CharField()
    total_price = serializers.DecimalField(max_digits=10, decimal_places=2)
    paid_amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    remaining_amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    is_fully_paid = serializers.BooleanField()


class CalendarResponseSerializer(serializers.Serializer):
    date_from = serializers.DateTimeField()
    date_to = serializers.DateTimeField()
    items = CalendarItemSerializer(many=True)


class DashboardOverviewSerializer(serializers.Serializer):
    date_from = serializers.DateTimeField()
    date_to = serializers.DateTimeField()
    court = serializers.IntegerField(allow_null=True)
    booking_counts_by_status = serializers.DictField(child=serializers.IntegerField())
    total_bookings = serializers.IntegerField()
    total_booking_value = serializers.DecimalField(max_digits=12, decimal_places=2)
    total_paid_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    total_remaining_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    transaction_total = serializers.DecimalField(max_digits=12, decimal_places=2)
    transaction_count = serializers.IntegerField()
    unsettled_transaction_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
    )
    unsettled_transaction_count = serializers.IntegerField()
    settled_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    settled_transaction_count = serializers.IntegerField()
    pending_settlement_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
    )
    pending_settlement_count = serializers.IntegerField()
    settled_settlement_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
    )
    settled_settlement_count = serializers.IntegerField()
    court_count = serializers.IntegerField()
    active_court_count = serializers.IntegerField()


class DashboardSummaryClubSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    slug = serializers.CharField()
    name = serializers.CharField()


class DashboardSummaryScopeSerializer(serializers.Serializer):
    role = serializers.CharField()
    court = serializers.IntegerField(allow_null=True)
    court_ids = serializers.ListField(child=serializers.IntegerField())
    financial_visible = serializers.BooleanField()


class DashboardSummaryPeriodSerializer(serializers.Serializer):
    date_from = serializers.DateTimeField()
    date_to = serializers.DateTimeField()


class DashboardSummaryMetricsSerializer(serializers.Serializer):
    court_count = serializers.IntegerField()
    active_court_count = serializers.IntegerField()
    total_bookings = serializers.IntegerField()
    hold_bookings = serializers.IntegerField()
    confirmed_bookings = serializers.IntegerField()
    completed_bookings = serializers.IntegerField()
    cancelled_bookings = serializers.IntegerField()
    no_show_bookings = serializers.IntegerField()
    expired_bookings = serializers.IntegerField()
    total_booking_value = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        allow_null=True,
    )
    total_paid_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        allow_null=True,
    )
    total_remaining_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        allow_null=True,
    )
    transaction_count = serializers.IntegerField(allow_null=True)
    transaction_total = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        allow_null=True,
    )
    unsettled_transaction_count = serializers.IntegerField(allow_null=True)
    unsettled_transaction_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        allow_null=True,
    )
    settled_transaction_count = serializers.IntegerField(allow_null=True)
    settled_transaction_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        allow_null=True,
    )
    pending_settlement_count = serializers.IntegerField(allow_null=True)
    pending_settlement_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        allow_null=True,
    )
    settled_settlement_count = serializers.IntegerField(allow_null=True)
    settled_settlement_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        allow_null=True,
    )


class DashboardSummaryCourtSerializer(serializers.Serializer):
    court = serializers.IntegerField()
    court_name = serializers.CharField()
    is_active = serializers.BooleanField()
    total_bookings = serializers.IntegerField()
    hold_bookings = serializers.IntegerField()
    confirmed_bookings = serializers.IntegerField()
    completed_bookings = serializers.IntegerField()
    cancelled_bookings = serializers.IntegerField()
    no_show_bookings = serializers.IntegerField()
    expired_bookings = serializers.IntegerField()
    total_booking_value = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        allow_null=True,
    )
    total_paid_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        allow_null=True,
    )
    total_remaining_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        allow_null=True,
    )
    transaction_count = serializers.IntegerField(allow_null=True)
    transaction_total = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        allow_null=True,
    )
    unsettled_transaction_count = serializers.IntegerField(allow_null=True)
    unsettled_transaction_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        allow_null=True,
    )
    settled_transaction_count = serializers.IntegerField(allow_null=True)
    settled_transaction_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        allow_null=True,
    )


class DashboardSummaryResponseSerializer(serializers.Serializer):
    club = DashboardSummaryClubSerializer()
    scope = DashboardSummaryScopeSerializer()
    period = DashboardSummaryPeriodSerializer()
    summary = DashboardSummaryMetricsSerializer()
    courts = DashboardSummaryCourtSerializer(many=True)


class RevenueSummaryItemSerializer(serializers.Serializer):
    period = serializers.CharField()
    transaction_total = serializers.DecimalField(max_digits=12, decimal_places=2)
    transaction_count = serializers.IntegerField()
    settled_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    settled_transaction_count = serializers.IntegerField()
    unsettled_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    unsettled_transaction_count = serializers.IntegerField()


class RevenueSummarySerializer(serializers.Serializer):
    date_from = serializers.DateTimeField()
    date_to = serializers.DateTimeField()
    group_by = serializers.CharField()
    results = RevenueSummaryItemSerializer(many=True)


class CourtUtilizationItemSerializer(serializers.Serializer):
    court = serializers.IntegerField()
    court_name = serializers.CharField()
    booking_count = serializers.IntegerField()
    booked_minutes = serializers.IntegerField()
    available_minutes = serializers.IntegerField()
    utilization_percentage = serializers.DecimalField(
        max_digits=7,
        decimal_places=2,
    )
    transaction_total = serializers.DecimalField(max_digits=12, decimal_places=2)


class CourtUtilizationSerializer(serializers.Serializer):
    date_from = serializers.DateTimeField()
    date_to = serializers.DateTimeField()
    results = CourtUtilizationItemSerializer(many=True)
