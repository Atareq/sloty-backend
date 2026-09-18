"""Contract tests for the single error-response envelope.

These lock the exact wire shape of every error the API can return. They exist so
that a future refactor of apps/common/exceptions.py cannot quietly change a key,
add one, or drop one — each of those is a breaking change for the frontend.
"""

from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.test import APIRequestFactory, APITestCase
from rest_framework.views import APIView

from apps.common.error_codes import ErrorCode
from apps.common.exceptions import (
    ErrorResponse,
    SlotyAPIException,
    sloty_exception_handler,
)


def _handle(exc):
    """Run an exception through the project handler and return its body."""
    request = APIRequestFactory().get("/")
    response = sloty_exception_handler(exc, {"request": request, "view": APIView()})
    return response


class ErrorResponseBuilderTests(APITestCase):
    """The builder itself owns which keys appear."""

    def test_minimal_payload_has_no_optional_keys(self):
        payload = ErrorResponse(code="SOME_CODE", message="Something went wrong.").as_dict()
        self.assertEqual(
            payload,
            {"success": False, "code": "SOME_CODE", "message": "Something went wrong."},
        )

    def test_field_errors_included_when_present(self):
        payload = ErrorResponse(
            code=ErrorCode.VALIDATION_ERROR,
            message="Invalid.",
            field_errors={"name": [{"code": "required", "message": "Required."}]},
        ).as_dict()
        self.assertIn("field_errors", payload)
        self.assertNotIn("details", payload)

    def test_empty_details_are_omitted(self):
        payload = ErrorResponse(code="X", message="m", details={}).as_dict()
        self.assertNotIn("details", payload)

    def test_lazy_message_is_coerced_to_text(self):
        from django.utils.translation import gettext_lazy as _

        payload = ErrorResponse(code="X", message=_("Lazy message.")).as_dict()
        self.assertIsInstance(payload["message"], str)


class ExceptionHandlerEnvelopeTests(APITestCase):
    """Every exception class the API raises produces one of two shapes."""

    def test_validation_error_shape(self):
        response = _handle(ValidationError({"name": ["This field is required."]}))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            set(response.data),
            {"success", "code", "message", "field_errors"},
        )
        self.assertFalse(response.data["success"])
        self.assertEqual(response.data["code"], ErrorCode.VALIDATION_ERROR)
        self.assertEqual(
            response.data["field_errors"]["name"],
            [{"code": "invalid", "message": "This field is required."}],
        )
        # Validation errors never carry a details object.
        self.assertNotIn("details", response.data)

    def test_non_field_validation_error_is_grouped(self):
        response = _handle(ValidationError("Bad input."))
        self.assertIn("non_field_errors", response.data["field_errors"])

    def test_sloty_api_exception_shape(self):
        response = _handle(
            SlotyAPIException(
                status_code=status.HTTP_409_CONFLICT,
                code="BOOKING_SLOT_UNAVAILABLE",
                message="Slot already booked.",
                details={"booking_id": 7},
            )
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(set(response.data), {"success", "code", "message", "details"})
        self.assertEqual(response.data["code"], "BOOKING_SLOT_UNAVAILABLE")
        self.assertEqual(response.data["message"], "Slot already booked.")
        self.assertEqual(response.data["details"], {"booking_id": 7})
        # Business errors never carry field_errors.
        self.assertNotIn("field_errors", response.data)

    def test_sloty_api_exception_without_details_omits_the_key(self):
        response = _handle(SlotyAPIException(code="SOME_RULE", message="Nope."))
        self.assertEqual(set(response.data), {"success", "code", "message"})

    def test_not_found_shape(self):
        response = _handle(NotFound())
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data["code"], ErrorCode.NOT_FOUND)
        self.assertFalse(response.data["success"])

    def test_permission_denied_shape(self):
        response = _handle(PermissionDenied())
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(set(response.data), {"success", "code", "message"})
        self.assertEqual(response.data["code"], "PERMISSION_DENIED")

    def test_unhandled_exception_is_left_to_django(self):
        # The handler returns None so Django's 500 machinery takes over rather
        # than dressing a crash up as a business error.
        self.assertIsNone(_handle(RuntimeError("boom")))


class ErrorCodeRegistryTests(APITestCase):
    """Codes are API keys, so they must stay upper-snake and unique."""

    def test_codes_are_upper_snake_case(self):
        for name, value in vars(ErrorCode).items():
            if name.startswith("_"):
                continue
            with self.subTest(code=name):
                self.assertEqual(value, value.upper())
                self.assertEqual(name, value)

    def test_codes_are_unique(self):
        values = [v for k, v in vars(ErrorCode).items() if not k.startswith("_")]
        self.assertEqual(len(values), len(set(values)))
