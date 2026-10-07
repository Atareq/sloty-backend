from io import BytesIO
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from PIL import Image

from apps.images.processing import ImageProcessingError, process_image


def image_upload(*, image_format="JPEG", size=(1200, 800), color="red", exif=None):
    content = BytesIO()
    image = Image.new("RGB", size, color)
    save_kwargs = {"format": image_format}
    if exif is not None:
        save_kwargs["exif"] = exif
    image.save(content, **save_kwargs)
    return SimpleUploadedFile("untrusted-name.bin", content.getvalue())


class ImageProcessingTests(SimpleTestCase):
    def output_image(self, content):
        output = Image.open(BytesIO(content))
        output.load()
        return output

    def test_jpeg_becomes_two_metadata_free_webp_variants(self):
        source = image_upload()

        processed = process_image(source)

        display = self.output_image(processed.display_bytes)
        thumbnail = self.output_image(processed.thumbnail_bytes)
        self.assertEqual(display.format, "WEBP")
        self.assertEqual(thumbnail.format, "WEBP")
        self.assertEqual(display.size, (1200, 800))
        self.assertEqual(thumbnail.size, (640, 427))
        self.assertEqual(display.getexif(), {})
        self.assertEqual((processed.width, processed.height), display.size)

    def test_png_and_webp_sources_are_normalized_to_webp(self):
        for image_format in ("PNG", "WEBP"):
            with self.subTest(image_format=image_format):
                processed = process_image(image_upload(image_format=image_format))
                self.assertEqual(
                    self.output_image(processed.display_bytes).format,
                    "WEBP",
                )

    def test_large_image_is_resized_without_changing_aspect_ratio(self):
        processed = process_image(image_upload(size=(2400, 1200)))

        self.assertEqual((processed.width, processed.height), (1920, 960))
        self.assertEqual(self.output_image(processed.thumbnail_bytes).size, (640, 320))

    def test_small_image_is_not_upscaled(self):
        processed = process_image(image_upload(size=(320, 200)))

        self.assertEqual((processed.width, processed.height), (320, 200))
        self.assertEqual(self.output_image(processed.thumbnail_bytes).size, (320, 200))

    def test_exif_orientation_is_applied_before_dimensions_are_reported(self):
        exif = Image.Exif()
        exif[274] = 6

        processed = process_image(image_upload(size=(400, 200), exif=exif))

        self.assertEqual((processed.width, processed.height), (200, 400))

    def assert_processing_error(self, upload, code):
        with self.assertRaises(ImageProcessingError) as raised:
            process_image(upload)
        self.assertEqual(raised.exception.code, code)

    def test_corrupt_and_unsupported_sources_are_rejected_from_decoded_content(self):
        self.assert_processing_error(
            SimpleUploadedFile("photo.jpg", b"not an image"),
            "INVALID_IMAGE_UPLOAD",
        )
        self.assert_processing_error(
            image_upload(image_format="BMP"),
            "UNSUPPORTED_IMAGE_FORMAT",
        )

    @override_settings(IMAGE_MAX_UPLOAD_BYTES=10)
    def test_oversized_source_is_rejected_before_decode(self):
        self.assert_processing_error(image_upload(), "IMAGE_UPLOAD_TOO_LARGE")

    @override_settings(IMAGE_MAX_WIDTH=100, IMAGE_MAX_HEIGHT=100)
    def test_unreasonable_dimensions_are_rejected(self):
        self.assert_processing_error(
            image_upload(size=(101, 100)),
            "IMAGE_DIMENSIONS_TOO_LARGE",
        )

    def test_decompression_bomb_warning_is_a_safe_dimension_error(self):
        with patch("apps.images.processing.PillowImage.MAX_IMAGE_PIXELS", 60):
            self.assert_processing_error(
                image_upload(size=(10, 10)),
                "IMAGE_DIMENSIONS_TOO_LARGE",
            )
