"""The small Cloudflare R2 boundary used by image-domain orchestration."""

import logging
from urllib.parse import quote

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)

IMAGE_CONTENT_TYPE = "image/webp"
IMAGE_CACHE_CONTROL = "public, max-age=31536000, immutable"


class ImageStorageError(Exception):
    """R2 is unavailable or rejected an image object operation."""


def _r2_configuration():
    configuration = {
        "endpoint": settings.R2_ENDPOINT,
        "access_key_id": settings.R2_ACCESS_KEY_ID,
        "secret_access_key": settings.R2_SECRET_ACCESS_KEY,
    }
    missing = [name for name, value in configuration.items() if not value]
    if missing:
        raise ImageStorageError("Image storage is not configured.")
    return configuration


class R2ImageStorage:
    def __init__(self):
        configuration = _r2_configuration()
        self.bucket_name = settings.R2_BUCKET_IMAGE
        if not self.bucket_name:
            raise ImageStorageError("Image storage is not configured.")
        try:
            import boto3

            self.client = boto3.client(
                "s3",
                endpoint_url=configuration["endpoint"],
                aws_access_key_id=configuration["access_key_id"],
                aws_secret_access_key=configuration["secret_access_key"],
                region_name="auto",
            )
        except Exception as exc:  # pragma: no cover - dependency/runtime boundary
            logger.exception("Unable to initialize the image storage client.")
            raise ImageStorageError("Image storage is unavailable.") from exc

    def upload_image_object(self, *, key, content):
        try:
            self.client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=content,
                ContentType=IMAGE_CONTENT_TYPE,
                CacheControl=IMAGE_CACHE_CONTROL,
            )
        except Exception as exc:  # pragma: no cover - exercised through fake storage
            logger.exception("Image object upload failed for key %s.", key)
            raise ImageStorageError("Image object upload failed.") from exc

    def delete_image_object(self, key):
        try:
            self.client.delete_object(Bucket=self.bucket_name, Key=key)
        except Exception as exc:  # pragma: no cover - exercised through fake storage
            logger.exception("Image object cleanup failed for key %s.", key)
            raise ImageStorageError("Image object cleanup failed.") from exc


def get_image_storage():
    return R2ImageStorage()


def build_public_image_url(key):
    public_base_url = settings.IMAGES_PUBLIC_BASE_URL
    if not public_base_url:
        raise ImproperlyConfigured("IMAGES_PUBLIC_BASE_URL must be configured.")
    return f"{public_base_url.rstrip('/')}/{quote(key, safe='/')}"
