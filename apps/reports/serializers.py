from datetime import datetime, time, timedelta

from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from apps.accounts.models import User
from apps.courts.models import Court
from apps.reports.constants import (
    ALLOWED_USAGE_STATUSES,
    DEFAULT_USAGE_STATUSES,
    PERIOD_ALL_DAY,
    PERIOD_CHOICES,
    PERIOD_CUSTOM,
    REPORT_MAX_RANGE_DAYS,
)


def date_bounds(date_value):
    start = datetime.combine(date_value, time.min)
    end = start + timedelta(days=1)
    timezone_value = timezone.get_current_timezone()
    return (
        timezone.make_aware(start, timezone_value),
        timezone.make_aware(end, timezone_value),
    )


class CourtUsageReportQuerySerializer(serializers.Serializer):
    date_from = serializers.DateField(required=True)
    date_to = serializers.DateField(required=True)
    court = serializers.PrimaryKeyRelatedField(
        queryset=Court.objects.all(),
        required=False,
        allow_null=True,
    )
    period = serializers.ChoiceField(
        choices=PERIOD_CHOICES,
        required=False,
        default=PERIOD_ALL_DAY,
    )
    hour_from = serializers.TimeField(required=False, allow_null=True)
    hour_to = serializers.TimeField(required=False, allow_null=True)
    staff = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        required=False,
        allow_null=True,
    )
    status = serializers.CharField(
        required=False,
        allow_blank=True,
    )

    def coded_error(self, field, message, code):
        return serializers.ValidationError(
            {field: [serializers.ErrorDetail(message, code=code)]}
        )

    def validate(self, attrs):
        if attrs["date_from"] > attrs["date_to"]:
            raise self.coded_error(
                "date_to",
                _("date_to must be on or after date_from."),
                "REPORT_DATE_RANGE_INVALID",
            )
        if (attrs["date_to"] - attrs["date_from"]).days + 1 > REPORT_MAX_RANGE_DAYS:
            raise self.coded_error(
                "date_to",
                _("Court usage reports are limited to 31 calendar days."),
                "REPORT_DATE_RANGE_TOO_LARGE",
            )

        period = attrs.get("period", PERIOD_ALL_DAY)
        if period == PERIOD_CUSTOM:
            if not attrs.get("hour_from") or not attrs.get("hour_to"):
                raise self.coded_error(
                    "hour_from",
                    _("hour_from and hour_to are required for custom period."),
                    "CUSTOM_REPORT_HOURS_REQUIRED",
                )
            if attrs["hour_from"] >= attrs["hour_to"]:
                raise self.coded_error(
                    "hour_to",
                    _("hour_to must be after hour_from."),
                    "INVALID_CUSTOM_REPORT_HOURS",
                )
        else:
            attrs["hour_from"] = None
            attrs["hour_to"] = None

        access = self.context["club_access"]
        court = attrs.get("court")
        if court is not None and not access.can_access_court(court):
            raise PermissionDenied(_("You cannot access this court."))

        staff = attrs.get("staff")
        if staff is not None and not access.can_filter_reports_by_staff(staff):
            raise self.coded_error(
                "staff",
                _("Selected staff must be active in this club."),
                "REPORT_STAFF_NOT_IN_CLUB",
            )

        status_value = attrs.get("status")
        if status_value == "":
            attrs["status"] = None
        elif status_value is not None and status_value not in set(
            ALLOWED_USAGE_STATUSES
        ):
            raise self.coded_error(
                "status",
                _("Invalid court usage status."),
                "INVALID_COURT_USAGE_STATUS",
            )
        date_from_start, date_from_end = date_bounds(attrs["date_from"])
        date_to_start, date_to_end = date_bounds(attrs["date_to"])

        attrs["range_start"] = date_from_start
        attrs["range_end"] = date_to_end
        attrs["included_statuses"] = (
            (attrs["status"],) if attrs.get("status") else DEFAULT_USAGE_STATUSES
        )
        return attrs


class ReportFinancialSerializer(serializers.Serializer):
    total_booking_value = serializers.DecimalField(max_digits=12, decimal_places=2)
    total_paid_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    total_remaining_amount = serializers.DecimalField(max_digits=12, decimal_places=2)


class ReportUsageBaseSerializer(serializers.Serializer):
    booking_count = serializers.IntegerField()
    occupied_minutes = serializers.IntegerField()
    available_minutes = serializers.IntegerField()
    utilization_percentage = serializers.DecimalField(max_digits=7, decimal_places=2)


class CourtUsageContextSerializer(serializers.Serializer):
    club_id = serializers.IntegerField()
    club_name = serializers.CharField()
    date_from = serializers.DateField()
    date_to = serializers.DateField()
    court = serializers.IntegerField(allow_null=True)
    court_name = serializers.CharField(allow_null=True)
    period = serializers.CharField()
    hour_from = serializers.TimeField(allow_null=True)
    hour_to = serializers.TimeField(allow_null=True)
    staff = serializers.IntegerField(allow_null=True)
    staff_name = serializers.CharField(allow_null=True)
    status = serializers.CharField(allow_null=True)
    included_statuses = serializers.ListField(child=serializers.CharField())
    demand_bucket_minutes = serializers.IntegerField()


class CourtUsageSummarySerializer(ReportUsageBaseSerializer):
    status_counts = serializers.DictField(child=serializers.IntegerField())
    financial = ReportFinancialSerializer()


class UsageByCourtSerializer(CourtUsageSummarySerializer):
    court = serializers.IntegerField()
    court_name = serializers.CharField()


class UsageByDaySerializer(ReportUsageBaseSerializer):
    date = serializers.DateField()
    financial = ReportFinancialSerializer()


class UsageByPeriodSerializer(ReportUsageBaseSerializer):
    period = serializers.CharField()
    hour_from = serializers.TimeField(allow_null=True, required=False)
    hour_to = serializers.TimeField(allow_null=True, required=False)


class DemandHourSerializer(ReportUsageBaseSerializer):
    hour_from = serializers.TimeField()
    hour_to = serializers.TimeField()


class StaffBookingActivitySerializer(serializers.Serializer):
    staff = serializers.IntegerField(allow_null=True)
    staff_name = serializers.CharField()
    booking_count = serializers.IntegerField()
    status_counts = serializers.DictField(child=serializers.IntegerField())
    occupied_minutes = serializers.IntegerField()
    financial = ReportFinancialSerializer()


class CourtUsageReportResponseSerializer(serializers.Serializer):
    context = CourtUsageContextSerializer()
    summary = CourtUsageSummarySerializer()
    usage_by_court = UsageByCourtSerializer(many=True)
    usage_by_day = UsageByDaySerializer(many=True)
    usage_by_period = UsageByPeriodSerializer(many=True)
    peak_hours = DemandHourSerializer(many=True)
    low_demand_hours = DemandHourSerializer(many=True)
    staff_booking_activity = StaffBookingActivitySerializer(many=True)
