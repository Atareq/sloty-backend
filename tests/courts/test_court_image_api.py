from io import BytesIO
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image as PillowImage
from rest_framework import status

from apps.courts.models import CourtImage
from apps.courts.services import cleanup_orphan_image
from apps.images.models import Image
from apps.images.storage import ImageStorageError
from tests.courts.test_court_api import CourtAPITestCase
from tests.profile_factories import grant_owner_club, grant_staff_court


class FakeImageStorage:
    def __init__(self, fail_upload_at=None, fail_delete=False):
        self.fail_upload_at = fail_upload_at
        self.fail_delete = fail_delete
        self.uploads = []
        self.deletions = []

    def upload_image_object(self, *, key, content):
        self.uploads.append((key, content))
        if len(self.uploads) == self.fail_upload_at:
            raise ImageStorageError("fake upload failure")

    def delete_image_object(self, key):
        self.deletions.append(key)
        if self.fail_delete:
            raise ImageStorageError("fake delete failure")


def uploaded_image(size=(1200, 800)):
    content = BytesIO()
    PillowImage.new("RGB", size, "blue").save(content, format="JPEG")
    return SimpleUploadedFile("client-controlled-name.jpg", content.getvalue())


@override_settings(IMAGES_PUBLIC_BASE_URL="https://images.sloty.test")
class CourtImageAPITests(CourtAPITestCase):
    def setUp(self):
        self.admin = self.create_platform_admin()
        self.owner = self.create_user()
        self.staff = self.create_user()
        self.outsider = self.create_user()
        self.club = self.create_club("Image Club", slug="image-club")
        self.other_club = self.create_club("Other Image Club", slug="other-image-club")
        self.court = self.create_court(self.club, "Image Court")
        self.other_court = self.create_court(self.other_club, "Other Image Court")
        grant_owner_club(self.owner, self.club)
        grant_staff_court(self.staff, self.court)
        self.storage = FakeImageStorage()

    def list_url(self, court=None, club=None):
        court = court or self.court
        club = club or self.club
        return f"/api/v1/clubs/{club.slug}/courts/{court.pk}/images/"

    def reorder_url(self):
        return f"/api/v1/clubs/{self.club.slug}/courts/{self.court.pk}/images/reorder/"

    def detail_url(self, court_image):
        return (
            f"/api/v1/clubs/{self.club.slug}/courts/{self.court.pk}/images/"
            f"{court_image.pk}/"
        )

    def upload(self):
        return self.client.post(
            self.list_url(), {"image": uploaded_image()}, format="multipart"
        )

    def test_authorized_upload_processes_once_uploads_two_objects_and_appends(self):
        self.client.force_authenticate(user=self.owner)
        with patch("apps.courts.services.get_image_storage", return_value=self.storage):
            first = self.upload()
            second = self.upload()

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Image.objects.count(), 2)
        self.assertEqual(CourtImage.objects.count(), 2)
        self.assertEqual([item.position for item in CourtImage.objects.all()], [0, 1])
        self.assertEqual(len(self.storage.uploads), 4)
        self.assertTrue(
            all(key.startswith("images/") for key, _ in self.storage.uploads)
        )
        self.assertEqual(first.data["image"]["width"], 1200)
        self.assertEqual(
            first.data["image"]["display_url"].split("/", 3)[:3],
            ["https:", "", "images.sloty.test"],
        )

    def test_staff_cannot_upload_and_wrong_court_scope_is_hidden(self):
        self.client.force_authenticate(user=self.staff)

        denied = self.upload()
        wrong_scope = self.client.get(self.list_url(self.other_court, self.other_club))

        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(wrong_scope.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(Image.objects.count(), 0)

    def test_list_returns_deterministic_order_and_derived_urls(self):
        first_image = Image.objects.create(
            display_key="images/one/display.webp",
            thumbnail_key="images/one/thumbnail.webp",
            width=1200,
            height=800,
        )
        second_image = Image.objects.create(
            display_key="images/two/display.webp",
            thumbnail_key="images/two/thumbnail.webp",
            width=640,
            height=400,
        )
        later = CourtImage.objects.create(
            court=self.court, image=second_image, position=1
        )
        first = CourtImage.objects.create(
            court=self.court, image=first_image, position=0
        )
        self.client.force_authenticate(user=self.staff)

        response = self.client.get(self.list_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([item["id"] for item in response.data], [first.id, later.id])
        self.assertEqual(
            response.data[0]["image"]["thumbnail_url"],
            "https://images.sloty.test/images/one/thumbnail.webp",
        )

    def test_reorder_rewrites_positions_to_contiguous_values(self):
        image_ids = []
        for position in range(3):
            image = Image.objects.create(
                display_key=f"images/{position}/display.webp",
                thumbnail_key=f"images/{position}/thumbnail.webp",
                width=1200,
                height=800,
            )
            image_ids.append(
                CourtImage.objects.create(
                    court=self.court, image=image, position=position
                ).id
            )
        self.client.force_authenticate(user=self.owner)

        response = self.client.put(
            self.reorder_url(), {"image_ids": image_ids[::-1]}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([item["id"] for item in response.data], image_ids[::-1])
        self.assertEqual(
            list(
                CourtImage.objects.filter(court=self.court).values_list(
                    "position", flat=True
                )
            ),
            [0, 1, 2],
        )

    def test_delete_detaches_shared_image_without_deleting_its_objects(self):
        shared = Image.objects.create(
            display_key="images/shared/display.webp",
            thumbnail_key="images/shared/thumbnail.webp",
            width=1200,
            height=800,
        )
        current = CourtImage.objects.create(court=self.court, image=shared, position=0)
        CourtImage.objects.create(court=self.other_court, image=shared, position=0)
        self.client.force_authenticate(user=self.owner)
        with patch("apps.courts.services.get_image_storage", return_value=self.storage):
            response = self.client.delete(self.detail_url(current))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertTrue(Image.objects.filter(pk=shared.pk).exists())
        self.assertEqual(self.storage.deletions, [])

    def test_orphan_cleanup_failure_keeps_the_image_row_for_retry(self):
        image = Image.objects.create(
            display_key="images/orphan/display.webp",
            thumbnail_key="images/orphan/thumbnail.webp",
            width=1200,
            height=800,
        )
        court_image = CourtImage.objects.create(
            court=self.court, image=image, position=0
        )
        self.client.force_authenticate(user=self.owner)
        failing_storage = FakeImageStorage(fail_delete=True)
        with patch(
            "apps.courts.services.get_image_storage", return_value=failing_storage
        ):
            response = self.client.delete(self.detail_url(court_image))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(CourtImage.objects.filter(pk=court_image.pk).exists())
        self.assertTrue(Image.objects.filter(pk=image.pk).exists())

        with patch("apps.courts.services.get_image_storage", return_value=self.storage):
            self.assertTrue(cleanup_orphan_image(image_id=image.pk))
        self.assertFalse(Image.objects.filter(pk=image.pk).exists())

    def test_upload_failure_never_creates_broken_database_references(self):
        self.client.force_authenticate(user=self.owner)
        failing_storage = FakeImageStorage(fail_upload_at=2)
        with patch(
            "apps.courts.services.get_image_storage", return_value=failing_storage
        ):
            response = self.upload()

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data["code"], "IMAGE_STORAGE_UNAVAILABLE")
        self.assertEqual(Image.objects.count(), 0)
        self.assertEqual(CourtImage.objects.count(), 0)
        self.assertEqual(len(failing_storage.deletions), 1)

    def test_first_upload_failure_creates_no_database_rows_or_cleanup_work(self):
        self.client.force_authenticate(user=self.owner)
        failing_storage = FakeImageStorage(fail_upload_at=1)
        with patch(
            "apps.courts.services.get_image_storage", return_value=failing_storage
        ):
            response = self.upload()

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(Image.objects.count(), 0)
        self.assertEqual(CourtImage.objects.count(), 0)
        self.assertEqual(failing_storage.deletions, [])

    def test_orphan_cleanup_removes_both_objects_then_the_image_row(self):
        image = Image.objects.create(
            display_key="images/clean/display.webp",
            thumbnail_key="images/clean/thumbnail.webp",
            width=1200,
            height=800,
        )
        court_image = CourtImage.objects.create(
            court=self.court, image=image, position=0
        )
        self.client.force_authenticate(user=self.owner)
        with patch("apps.courts.services.get_image_storage", return_value=self.storage):
            response = self.client.delete(self.detail_url(court_image))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Image.objects.filter(pk=image.pk).exists())
        self.assertEqual(
            self.storage.deletions,
            ["images/clean/display.webp", "images/clean/thumbnail.webp"],
        )

    def test_database_failure_after_both_uploads_attempts_both_cleanups(self):
        self.client.force_authenticate(user=self.owner)
        with patch("apps.courts.services.get_image_storage", return_value=self.storage):
            with patch(
                "apps.courts.services.Image.objects.create", side_effect=RuntimeError
            ):
                with self.assertRaises(RuntimeError):
                    self.upload()

        self.assertEqual(Image.objects.count(), 0)
        self.assertEqual(CourtImage.objects.count(), 0)
        self.assertEqual(len(self.storage.uploads), 2)
        self.assertEqual(len(self.storage.deletions), 2)
