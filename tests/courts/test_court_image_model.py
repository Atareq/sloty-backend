from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from apps.clubs.models import Club
from apps.courts.models import Court, CourtImage
from apps.images.models import Image


class CourtImageModelTests(TestCase):
    def create_court(self, name):
        club = Club.objects.create(
            name=f"{name} Club",
            governorate="ASSIUT",
            city="ASSIUT_MARKAZ",
        )
        return Court.objects.create(
            club=club,
            name=name,
            default_price="250.00",
        )

    def create_image(self, key):
        return Image.objects.create(
            display_key=f"images/{key}/display.webp",
            thumbnail_key=f"images/{key}/thumbnail.webp",
            width=1200,
            height=800,
        )

    def test_image_exists_independently_with_canonical_creation_timestamp(self):
        image = self.create_image("independent")

        self.assertEqual(
            {field.name for field in Image._meta.fields},
            {"id", "display_key", "thumbnail_key", "width", "height", "created"},
        )
        self.assertTrue(Image._meta.get_field("created").auto_now_add)
        self.assertIsNotNone(image.created)

    def test_court_image_connects_court_to_image(self):
        court_image = CourtImage.objects.create(
            court=self.create_court("Connected Court"),
            image=self.create_image("connected"),
            position=0,
        )

        self.assertEqual(court_image.court.court_images.get(), court_image)
        self.assertEqual(court_image.image.court_images.get(), court_image)

    def test_court_cannot_attach_same_image_twice(self):
        court = self.create_court("Unique Court")
        image = self.create_image("unique")
        CourtImage.objects.create(court=court, image=image, position=0)

        with self.assertRaises(IntegrityError), transaction.atomic():
            CourtImage.objects.create(court=court, image=image, position=1)

    def test_different_courts_can_attach_same_image(self):
        image = self.create_image("shared")
        first = CourtImage.objects.create(
            court=self.create_court("First Court"), image=image, position=0
        )
        second = CourtImage.objects.create(
            court=self.create_court("Second Court"), image=image, position=0
        )

        self.assertEqual(set(image.court_images.all()), {first, second})

    def test_court_images_order_by_position_then_id(self):
        court = self.create_court("Ordered Court")
        later = CourtImage.objects.create(
            court=court, image=self.create_image("later"), position=2
        )
        first_same_position = CourtImage.objects.create(
            court=court, image=self.create_image("first-same"), position=1
        )
        second_same_position = CourtImage.objects.create(
            court=court, image=self.create_image("second-same"), position=1
        )
        earliest = CourtImage.objects.create(
            court=court, image=self.create_image("earliest"), position=0
        )

        self.assertEqual(
            list(CourtImage.objects.filter(court=court)),
            [earliest, first_same_position, second_same_position, later],
        )

    def test_attached_image_is_protected_from_direct_deletion(self):
        image = self.create_image("protected")
        CourtImage.objects.create(
            court=self.create_court("Protected Court"), image=image, position=0
        )

        with self.assertRaises(ProtectedError):
            image.delete()

    def test_removing_court_image_does_not_remove_image(self):
        image = self.create_image("detached")
        court_image = CourtImage.objects.create(
            court=self.create_court("Detached Court"), image=image, position=0
        )

        court_image.delete()

        self.assertTrue(Image.objects.filter(pk=image.pk).exists())

    def test_court_image_uses_canonical_creation_timestamp(self):
        self.assertTrue(CourtImage._meta.get_field("created").auto_now_add)
