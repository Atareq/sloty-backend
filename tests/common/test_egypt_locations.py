from django.test import SimpleTestCase

from apps.common.egypt_locations import (
    GOVERNORATES,
    get_all_city_choices,
    get_city_choices,
    get_governorate_choices,
    is_valid_city,
    is_valid_city_for_governorate,
    is_valid_governorate,
)


class EgyptLocationConstantsTests(SimpleTestCase):
    def test_all_required_governorates_exist(self):
        governorate_codes = {governorate["code"] for governorate in GOVERNORATES}

        self.assertEqual(len(governorate_codes), 27)
        self.assertIn("ASSIUT", governorate_codes)
        self.assertIn("SOHAG", governorate_codes)
        self.assertIn("MINYA", governorate_codes)
        self.assertIn("QENA", governorate_codes)

    def test_each_governorate_has_at_least_one_city(self):
        for governorate in GOVERNORATES:
            self.assertGreaterEqual(len(governorate["cities"]), 1)

    def test_priority_upper_egypt_governorates_have_detailed_city_lists(self):
        expected_counts = {
            "ASSIUT": 15,
            "MINYA": 15,
            "SOHAG": 19,
            "QENA": 11,
        }
        city_counts = {
            governorate["code"]: len(governorate["cities"])
            for governorate in GOVERNORATES
        }

        for governorate_code, expected_count in expected_counts.items():
            self.assertGreaterEqual(city_counts[governorate_code], expected_count)

    def test_choice_helpers_return_django_choices(self):
        governorate_choices = get_governorate_choices()
        city_choices = get_all_city_choices()
        assiut_choices = get_city_choices("ASSIUT")

        self.assertIn(("ASSIUT", "Assiut"), governorate_choices)
        self.assertIn(("ASSIUT_MARKAZ", "Assiut Markaz"), city_choices)
        self.assertIn(("ASSIUT_MARKAZ", "Assiut Markaz"), assiut_choices)

    def test_validation_helpers_accept_and_reject_expected_codes(self):
        self.assertTrue(is_valid_governorate("ASSIUT"))
        self.assertFalse(is_valid_governorate("UNKNOWN"))
        self.assertTrue(is_valid_city("ASSIUT_MARKAZ"))
        self.assertFalse(is_valid_city("Assiut"))
        self.assertTrue(is_valid_city_for_governorate("ASSIUT", "ASSIUT_MARKAZ"))
        self.assertFalse(is_valid_city_for_governorate("ASSIUT", "SOHAG_MARKAZ"))
