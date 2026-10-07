from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from apps.images.storage import (
    IMAGE_CACHE_CONTROL,
    IMAGE_CONTENT_TYPE,
    R2ImageStorage,
    build_public_image_url,
)


@override_settings(
    R2_ENDPOINT="https://account.r2.cloudflarestorage.com",
    R2_ACCESS_KEY_ID="access-key",
    R2_SECRET_ACCESS_KEY="secret-key",
    R2_BUCKET_IMAGE="sloty-images",
)
class R2ImageStorageTests(SimpleTestCase):
    @patch("boto3.client")
    def test_storage_uses_separate_webp_objects_with_immutable_caching(
        self, client_factory
    ):
        client = Mock()
        client_factory.return_value = client
        storage = R2ImageStorage()

        client_factory.assert_called_once_with(
            "s3",
            endpoint_url="https://account.r2.cloudflarestorage.com",
            aws_access_key_id="access-key",
            aws_secret_access_key="secret-key",
            region_name="auto",
        )

        storage.upload_image_object(key="images/a/display.webp", content=b"display")
        storage.upload_image_object(key="images/a/thumbnail.webp", content=b"thumbnail")
        storage.delete_image_object("images/a/display.webp")

        self.assertEqual(client.put_object.call_count, 2)
        self.assertEqual(
            client.put_object.call_args_list[0].kwargs,
            {
                "Bucket": "sloty-images",
                "Key": "images/a/display.webp",
                "Body": b"display",
                "ContentType": IMAGE_CONTENT_TYPE,
                "CacheControl": IMAGE_CACHE_CONTROL,
            },
        )
        client.delete_object.assert_called_once_with(
            Bucket="sloty-images", Key="images/a/display.webp"
        )

    @override_settings(IMAGES_PUBLIC_BASE_URL="https://images.sloty.test/")
    def test_public_url_is_derived_from_the_key_without_storage_credentials(self):
        self.assertEqual(
            build_public_image_url("images/a b/display.webp"),
            "https://images.sloty.test/images/a%20b/display.webp",
        )

    @override_settings(R2_BUCKET_IMAGE="", R2_BUCKET_BACKUP="sloty-prod-backups")
    def test_image_storage_does_not_fall_back_to_backup_bucket(self):
        from apps.images.storage import ImageStorageError

        with self.assertRaises(ImageStorageError):
            R2ImageStorage()
