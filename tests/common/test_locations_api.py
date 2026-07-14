from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase


class EgyptLocationAPITests(APITestCase):
    def test_locations_endpoint_is_public_and_returns_governorates(self):
        response = self.client.get(reverse("egypt-locations"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        governorates = response.data["governorates"]
        governorate_codes = {governorate["code"] for governorate in governorates}
        self.assertEqual(len(governorates), 27)
        self.assertIn("ASSIUT", governorate_codes)
        self.assertIn("SOHAG", governorate_codes)
        self.assertIn("MINYA", governorate_codes)
        self.assertIn("QENA", governorate_codes)

    def test_locations_endpoint_contains_priority_city_codes(self):
        response = self.client.get(reverse("egypt-locations"))

        city_codes = {
            city["code"]
            for governorate in response.data["governorates"]
            for city in governorate["cities"]
        }
        self.assertIn("ASSIUT_MARKAZ", city_codes)
        self.assertIn("SOHAG_MARKAZ", city_codes)
        self.assertIn("MINYA_MARKAZ", city_codes)
        self.assertIn("QENA_CITY", city_codes)

    def test_locations_endpoint_rejects_post(self):
        response = self.client.post(reverse("egypt-locations"), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
