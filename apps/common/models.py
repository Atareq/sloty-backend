from django.db import models
from django_extensions.db.models import TimeStampedModel


class SlotyCreatedModel(models.Model):
    """Immutable / append-only records. Insert time only."""

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True


class SlotyTimeStampedModel(TimeStampedModel):
    """Mutable business entities. Uses django-extensions created/modified."""

    class Meta:
        abstract = True
