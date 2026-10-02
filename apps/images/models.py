from django.db import models

from apps.common.models import SlotyCreatedModel


class Image(SlotyCreatedModel):
    display_key = models.CharField(max_length=512, unique=True)
    thumbnail_key = models.CharField(max_length=512, unique=True)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
