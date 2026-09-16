from django.contrib.auth.models import AbstractUser
from django.db import models
from phonenumber_field.modelfields import PhoneNumberField


class User(AbstractUser):
    phone_number = PhoneNumberField(blank=True, null=True)
    created_by = models.ForeignKey(
        "self",
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="created_users",
    )

    REQUIRED_FIELDS = ["email"]

    @property
    def role(self):
        """Convenient access to the single stored application role."""
        profile = getattr(self, "profile", None)
        return profile.role if profile else None

    def is_platform_super_admin(self) -> bool:
        profile = getattr(self, "profile", None)
        return bool(profile and profile.is_enabled and profile.role == "ADMIN")
