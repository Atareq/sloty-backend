"""Tests for Sloty canonical weekday contract.

Canonical contract:
    Saturday  = 0
    Sunday    = 1
    Monday    = 2
    Tuesday   = 3
    Wednesday = 4
    Thursday  = 5
    Friday    = 6
"""

from datetime import date

from django.test import SimpleTestCase

from apps.common.weekdays import to_python_weekday, to_sloty_weekday
from apps.courts.models import CourtWorkingHour


class WeekdayContractTests(SimpleTestCase):
    """Verify Sloty canonical weekday definitions and conversion helpers."""

    def test_enum_values_match_canonical_contract(self):
        self.assertEqual(CourtWorkingHour.Weekday.SATURDAY, 0)
        self.assertEqual(CourtWorkingHour.Weekday.SUNDAY, 1)
        self.assertEqual(CourtWorkingHour.Weekday.MONDAY, 2)
        self.assertEqual(CourtWorkingHour.Weekday.TUESDAY, 3)
        self.assertEqual(CourtWorkingHour.Weekday.WEDNESDAY, 4)
        self.assertEqual(CourtWorkingHour.Weekday.THURSDAY, 5)
        self.assertEqual(CourtWorkingHour.Weekday.FRIDAY, 6)

    def test_enum_labels(self):
        self.assertEqual(CourtWorkingHour.Weekday.SATURDAY.label, "Saturday")
        self.assertEqual(CourtWorkingHour.Weekday.SUNDAY.label, "Sunday")
        self.assertEqual(CourtWorkingHour.Weekday.MONDAY.label, "Monday")
        self.assertEqual(CourtWorkingHour.Weekday.TUESDAY.label, "Tuesday")
        self.assertEqual(CourtWorkingHour.Weekday.WEDNESDAY.label, "Wednesday")
        self.assertEqual(CourtWorkingHour.Weekday.THURSDAY.label, "Thursday")
        self.assertEqual(CourtWorkingHour.Weekday.FRIDAY.label, "Friday")

    def test_to_sloty_weekday_converts_all_python_weekdays(self):
        # Python: Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5, Sun=6
        expected = {
            0: 2,  # Monday -> Sloty Monday (2)
            1: 3,  # Tuesday -> Sloty Tuesday (3)
            2: 4,  # Wednesday -> Sloty Wednesday (4)
            3: 5,  # Thursday -> Sloty Thursday (5)
            4: 6,  # Friday -> Sloty Friday (6)
            5: 0,  # Saturday -> Sloty Saturday (0)
            6: 1,  # Sunday -> Sloty Sunday (1)
        }
        for py_wd, sloty_wd in expected.items():
            with self.subTest(python_weekday=py_wd):
                self.assertEqual(to_sloty_weekday(py_wd), sloty_wd)

    def test_to_python_weekday_converts_all_sloty_weekdays(self):
        expected = {
            0: 5,  # Saturday -> Python Saturday (5)
            1: 6,  # Sunday -> Python Sunday (6)
            2: 0,  # Monday -> Python Monday (0)
            3: 1,  # Tuesday -> Python Tuesday (1)
            4: 2,  # Wednesday -> Python Wednesday (2)
            5: 3,  # Thursday -> Python Thursday (3)
            6: 4,  # Friday -> Python Friday (4)
        }
        for sloty_wd, py_wd in expected.items():
            with self.subTest(sloty_weekday=sloty_wd):
                self.assertEqual(to_python_weekday(sloty_wd), py_wd)

    def test_round_trip_conversion(self):
        for sloty_wd in range(7):
            with self.subTest(sloty_weekday=sloty_wd):
                self.assertEqual(
                    to_sloty_weekday(to_python_weekday(sloty_wd)), sloty_wd
                )

        for py_wd in range(7):
            with self.subTest(python_weekday=py_wd):
                self.assertEqual(to_python_weekday(to_sloty_weekday(py_wd)), py_wd)

    def test_calendar_date_mapping(self):
        # 2026-09-19 is Saturday through 2026-09-25 which is Friday
        dates_and_expected_sloty_weekdays = [
            (date(2026, 9, 19), CourtWorkingHour.Weekday.SATURDAY),
            (date(2026, 9, 20), CourtWorkingHour.Weekday.SUNDAY),
            (date(2026, 9, 21), CourtWorkingHour.Weekday.MONDAY),
            (date(2026, 9, 22), CourtWorkingHour.Weekday.TUESDAY),
            (date(2026, 9, 23), CourtWorkingHour.Weekday.WEDNESDAY),
            (date(2026, 9, 24), CourtWorkingHour.Weekday.THURSDAY),
            (date(2026, 9, 25), CourtWorkingHour.Weekday.FRIDAY),
        ]
        for d, expected_sloty_wd in dates_and_expected_sloty_weekdays:
            with self.subTest(date=d):
                self.assertEqual(to_sloty_weekday(d.weekday()), expected_sloty_wd)
