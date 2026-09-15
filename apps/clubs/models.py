from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import slugify
from phonenumber_field.modelfields import PhoneNumberField

from apps.common.egypt_locations import (
    get_all_city_choices,
    get_governorate_choices,
    is_valid_city,
    is_valid_city_for_governorate,
    is_valid_governorate,
)


def generate_unique_club_slug(name: str, *, exclude_pk=None) -> str:
    base_slug = slugify(name) or "club"
    slug = base_slug
    suffix = 2
    queryset = Club.objects.all()
    if exclude_pk is not None:
        queryset = queryset.exclude(pk=exclude_pk)
    while queryset.filter(slug=slug).exists():
        slug = f"{base_slug}-{suffix}"
        suffix += 1
    return slug


class Club(models.Model):
    name = models.CharField(max_length=255, db_index=True)
    slug = models.SlugField(max_length=120, unique=True, db_index=True)
    governorate = models.CharField(
        max_length=64, choices=get_governorate_choices(), db_index=True
    )
    city = models.CharField(
        max_length=120, choices=get_all_city_choices(), db_index=True
    )
    address = models.TextField(blank=True)
    phone_number = PhoneNumberField(blank=True, null=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="created_clubs",
    )
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["city"]),
            models.Index(fields=["is_active"]),
        ]

    authorization_config = {
        "scopes": {"club": {"path": "self"}},
        "default_scope": "club",
        "select_related": ("created_by",),
        "prefetch_related": (),
    }

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = generate_unique_club_slug(self.name, exclude_pk=self.pk)
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        errors = {}
        if not is_valid_governorate(self.governorate):
            errors["governorate"] = "Invalid governorate choice."
        if not is_valid_city(self.city):
            errors["city"] = "Invalid city choice."
        elif self.governorate and not is_valid_city_for_governorate(
            self.governorate, self.city
        ):
            errors["city"] = "City must belong to the selected governorate."
        if errors:
            raise ValidationError(errors)
