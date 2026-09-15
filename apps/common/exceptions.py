from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.exceptions import (
    APIException,
    AuthenticationFailed,
    ValidationError,
)
from rest_framework.views import exception_handler as drf_exception_handler
from rest_framework_simplejwt.exceptions import InvalidToken


class SlotyAPIException(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = _("An API error occurred.")
    default_code = "API_ERROR"

    def __init__(self, *, status_code=None, code=None, message=None, details=None):
        if status_code is not None:
            self.status_code = status_code
        self.api_code = code or self.default_code
        self.details = details or {}
        super().__init__(detail=message or self.default_detail, code=self.api_code)


def _error_code(value):
    code = getattr(value, "code", None)
    if code is None:
        return "invalid"
    return str(code)


def _error_message(value):
    return str(value)


def _first_message_from_errors(field_errors):
    for errors in field_errors.values():
        if errors:
            return errors[0]["message"]
    return str(_("Validation error."))


def _normalize_error_list(value):
    if isinstance(value, (list, tuple)):
        return [
            {
                "code": _error_code(item),
                "message": _error_message(item),
            }
            for item in value
        ]
    return [
        {
            "code": _error_code(value),
            "message": _error_message(value),
        }
    ]


def _normalize_validation_detail(detail):
    if isinstance(detail, dict):
        return {
            str(field): _normalize_error_list(errors)
            for field, errors in detail.items()
        }
    return {"non_field_errors": _normalize_error_list(detail)}


def _first_detail_code(detail):
    code = getattr(detail, "code", None)
    if code is not None:
        return str(code)
    if isinstance(detail, dict):
        for value in detail.values():
            nested_code = _first_detail_code(value)
            if nested_code:
                return nested_code
    if isinstance(detail, (list, tuple)):
        for value in detail:
            nested_code = _first_detail_code(value)
            if nested_code:
                return nested_code
    return ""


def _api_exception_code(exc, response):
    if hasattr(exc, "api_code"):
        return exc.api_code
    if isinstance(exc, InvalidToken):
        messages = getattr(exc, "detail", {}).get("messages", [])
        for message in messages:
            if str(message.get("message", "")).lower() == "token is expired":
                return "SESSION_EXPIRED"
        return "TOKEN_NOT_VALID"
    if isinstance(exc, AuthenticationFailed):
        detail = getattr(exc, "detail", None)
        detail_code = _first_detail_code(detail)
        structured_code = ""
        if isinstance(detail, dict) and "code" in detail:
            structured_code = str(detail["code"]).lower()
        detail_text = str(detail).lower()
        if detail_code == "user_inactive" or "user is inactive" in detail_text:
            return "USER_INACTIVE"
        if detail_code == "user_not_found" or "user not found" in detail_text:
            return "USER_DELETED"
        if detail_code == "password_changed" or structured_code == "password_changed":
            return "PASSWORD_CHANGED"
    status_code = getattr(exc, "status_code", response.status_code)
    if status_code == status.HTTP_404_NOT_FOUND:
        return "NOT_FOUND"
    code = getattr(exc, "default_code", "API_ERROR")
    return str(code).upper()


def sloty_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    if isinstance(exc, ValidationError):
        field_errors = _normalize_validation_detail(exc.detail)
        response.data = {
            "success": False,
            "code": "VALIDATION_ERROR",
            "message": _first_message_from_errors(field_errors),
            "field_errors": field_errors,
        }
        return response

    response.data = {
        "success": False,
        "code": _api_exception_code(exc, response),
        "message": str(response.data.get("detail", exc)),
    }
    if hasattr(exc, "details") and exc.details:
        response.data["details"] = exc.details
    return response
