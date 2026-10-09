"""In-memory source validation and WebP variant generation."""

import warnings
from dataclasses import dataclass
from io import BytesIO

from django.conf import settings
from PIL import Image as PillowImage
from PIL import ImageOps, UnidentifiedImageError

DISPLAY_MAX_LONG_EDGE = 1920
DISPLAY_WEBP_QUALITY = 84
THUMBNAIL_MAX_LONG_EDGE = 640
THUMBNAIL_WEBP_QUALITY = 78
SUPPORTED_SOURCE_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})


class ImageProcessingError(Exception):
    """A safe, stable validation error for an uploaded source image."""

    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class ProcessedImage:
    display_bytes: bytes
    thumbnail_bytes: bytes
    width: int
    height: int


def _uploaded_size(uploaded_file):
    size = getattr(uploaded_file, "size", None)
    if size is None:
        position = uploaded_file.tell()
        uploaded_file.seek(0, 2)
        size = uploaded_file.tell()
        uploaded_file.seek(position)
    return size


def _validate_source_size(uploaded_file):
    if _uploaded_size(uploaded_file) > settings.IMAGE_MAX_UPLOAD_BYTES:
        raise ImageProcessingError(
            "IMAGE_UPLOAD_TOO_LARGE",
            "The uploaded image exceeds the maximum allowed file size.",
        )


def _open_source_image(uploaded_file):
    try:
        uploaded_file.seek(0)
        with warnings.catch_warnings():
            warnings.simplefilter("error", PillowImage.DecompressionBombWarning)
            source = PillowImage.open(uploaded_file)
            source_format = source.format
            if source_format not in SUPPORTED_SOURCE_FORMATS:
                raise ImageProcessingError(
                    "UNSUPPORTED_IMAGE_FORMAT",
                    "Only JPEG, PNG, and WebP images are supported.",
                )
            source.load()
    except ImageProcessingError:
        raise
    except PillowImage.DecompressionBombWarning:
        raise ImageProcessingError(
            "IMAGE_DIMENSIONS_TOO_LARGE",
            "The uploaded image dimensions are too large.",
        )
    except (PillowImage.DecompressionBombError, UnidentifiedImageError, OSError):
        raise ImageProcessingError(
            "INVALID_IMAGE_UPLOAD",
            "The uploaded file is not a valid supported image.",
        )
    finally:
        uploaded_file.seek(0)

    return source


def _validate_dimensions(image):
    width, height = image.size
    if (
        width <= 0
        or height <= 0
        or width > settings.IMAGE_MAX_WIDTH
        or height > settings.IMAGE_MAX_HEIGHT
        or width * height > settings.IMAGE_MAX_PIXELS
    ):
        raise ImageProcessingError(
            "IMAGE_DIMENSIONS_TOO_LARGE",
            "The uploaded image dimensions are too large.",
        )


def _normalize_image(image):
    """Apply EXIF orientation and flatten transparency onto white deterministically."""
    image = ImageOps.exif_transpose(image)
    rgba = image.convert("RGBA")
    background = PillowImage.new("RGBA", rgba.size, (255, 255, 255, 255))
    background.alpha_composite(rgba)
    return background.convert("RGB")


def _resized(image, max_long_edge):
    copy = image.copy()
    copy.thumbnail((max_long_edge, max_long_edge), PillowImage.Resampling.LANCZOS)
    return copy


def _encode_webp(image, quality):
    output = BytesIO()
    image.save(
        output,
        format="WEBP",
        quality=quality,
        method=6,
    )
    return output.getvalue()


def process_image(uploaded_file):
    """Validate one source upload and return its permanent display variants."""
    _validate_source_size(uploaded_file)
    source = _open_source_image(uploaded_file)
    _validate_dimensions(source)
    normalized = _normalize_image(source)
    display = _resized(normalized, DISPLAY_MAX_LONG_EDGE)
    thumbnail = _resized(normalized, THUMBNAIL_MAX_LONG_EDGE)
    return ProcessedImage(
        display_bytes=_encode_webp(display, DISPLAY_WEBP_QUALITY),
        thumbnail_bytes=_encode_webp(thumbnail, THUMBNAIL_WEBP_QUALITY),
        width=display.width,
        height=display.height,
    )
