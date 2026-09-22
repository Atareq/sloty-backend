from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from phonenumber_field.modelfields import PhoneNumberField
from phonenumber_field.phonenumber import to_python


class UserManager(BaseUserManager):
    """Phone-first account manager. Does not create Profile rows."""

    use_in_migrations = True

    def _normalize_phone(self, phone_number):
        if not phone_number:
            raise ValueError("Users must have a phone number.")
        phone = to_python(phone_number)
        if phone is None or not phone.is_valid():
            raise ValueError("Users must have a valid phone number.")
        return phone

    def _create_user(self, phone_number, email, password, **extra_fields):
        if not email:
            raise ValueError("Users must have an email address.")
        email = self.normalize_email(email)
        user = self.model(
            phone_number=self._normalize_phone(phone_number),
            email=email,
            **extra_fields,
        )
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, phone_number, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        extra_fields.setdefault("is_active", True)
        return self._create_user(phone_number, email, password, **extra_fields)

    def create_superuser(self, phone_number, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(phone_number, email, password, **extra_fields)


class User(AbstractUser):
    username = None
    phone_number = PhoneNumberField(unique=True)
    email = models.EmailField()

    USERNAME_FIELD = "phone_number"
    REQUIRED_FIELDS = ["email"]

    objects = UserManager()

    def get_display_name(self):
        full_name = self.get_full_name().strip()
        if full_name:
            return full_name
        return str(self.phone_number)

    def __str__(self):
        return self.get_display_name()
