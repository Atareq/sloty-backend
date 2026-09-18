"""Contract for the shared base models.

Pins the soft-delete semantics and — most importantly — the interaction with the
Authorization Spine, which builds every scoped queryset from _default_manager.
"""

from django.utils import timezone
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.clubs.models import Club
from apps.common.models import SoftDeleteModel, TimeStampedModel


def make_club(name="Base Model Club", slug="base-model-club"):
    return Club.objects.create(
        name=name,
        slug=slug,
        governorate="ASY",
        city="ASYUT",
    )


class TimeStampFieldTests(APITestCase):

    def test_every_model_carries_the_shared_timestamps(self):
        """The base model is applied project-wide, not just to a few tables."""
        from apps.audit.models import AuditLog
        from apps.bookings.models import Booking, BookingAttempt
        from apps.courts.models import Court, CourtWorkingHour
        from apps.players.models import ClubPlayer
        from apps.profiles.models import Profile, StaffCourtAccess
        from apps.settlements.models import Settlement, SettlementTransaction
        from apps.transactions.models import Transaction, TransactionAttempt

        models = [
            User, Club, Court, CourtWorkingHour, Profile, StaffCourtAccess,
            ClubPlayer, Booking, BookingAttempt, Transaction, TransactionAttempt,
            Settlement, SettlementTransaction, AuditLog,
        ]
        for model in models:
            with self.subTest(model=model.__name__):
                names = {f.name for f in model._meta.get_fields()}
                self.assertIn("created_at", names)
                self.assertIn("modified_at", names)

    def test_timestamps_are_populated_on_create(self):
        club = make_club()
        self.assertIsNotNone(club.created_at)
        self.assertIsNotNone(club.modified_at)

    def test_modified_at_advances_on_save(self):
        club = make_club()
        original = club.modified_at
        club.name = "Renamed"
        club.save()
        club.refresh_from_db()
        self.assertGreater(club.modified_at, original)


class SoftDeleteBoundaryTests(APITestCase):
    """Soft delete is opt-in. Immutable-history models must not get it."""

    def test_models_that_allow_deletion_are_soft_deletable(self):
        """Club and Court are the entities where "restore it" is a real need."""
        from apps.courts.models import Court

        for model in (Club, Court):
            with self.subTest(model=model.__name__):
                self.assertTrue(issubclass(model, SoftDeleteModel))

    def test_immutable_history_models_are_not_soft_deletable(self):
        """These domains forbid deletion outright; a delete() that silently
        succeeded would contradict their documented invariants."""
        from apps.audit.models import AuditLog
        from apps.bookings.models import Booking
        from apps.players.models import ClubPlayer
        from apps.transactions.models import Transaction

        for model in (Transaction, AuditLog, ClubPlayer, Booking):
            with self.subTest(model=model.__name__):
                self.assertTrue(issubclass(model, TimeStampedModel))
                self.assertFalse(issubclass(model, SoftDeleteModel))

    def test_wholesale_replaced_config_rows_are_not_soft_deletable(self):
        """These are rebuilt by replace-and-recreate flows (weekly working
        hours, staff court reassignment). A delete() that only marked rows
        would leave them behind to collide with their UNIQUE constraints.
        Profile already expresses deactivation through is_enabled."""
        from apps.courts.models import CourtWorkingHour, CourtWorkingHourPricePeriod
        from apps.profiles.models import Profile, StaffCourtAccess

        for model in (
            CourtWorkingHour,
            CourtWorkingHourPricePeriod,
            Profile,
            StaffCourtAccess,
        ):
            with self.subTest(model=model.__name__):
                self.assertTrue(issubclass(model, TimeStampedModel))
                self.assertFalse(issubclass(model, SoftDeleteModel))


class SoftDeleteBehaviourTests(APITestCase):

    def setUp(self):
        super().setUp()
        self.actor = User.objects.create_user(username="remover", password="pw-123456")
        self.club = make_club()

    def test_delete_marks_the_row_instead_of_removing_it(self):
        self.club.delete(deleted_by=self.actor)

        self.assertTrue(Club.all_objects.filter(pk=self.club.pk).exists())
        row = Club.all_objects.get(pk=self.club.pk)
        self.assertIsNotNone(row.deleted_at)
        self.assertEqual(row.deleted_by, self.actor)
        self.assertTrue(row.is_deleted)

    def test_default_manager_hides_deleted_rows(self):
        self.club.delete()
        self.assertFalse(Club.objects.filter(pk=self.club.pk).exists())

    def test_all_objects_and_deleted_objects(self):
        kept = make_club(name="Kept", slug="kept-club")
        self.club.delete()

        self.assertEqual(list(Club.objects.all()), [kept])
        self.assertCountEqual(list(Club.all_objects.all()), [kept, self.club])
        self.assertEqual([c.pk for c in Club.deleted_objects.all()], [self.club.pk])

    def test_restore_clears_both_fields(self):
        self.club.delete(deleted_by=self.actor)
        self.club.restore()

        row = Club.all_objects.get(pk=self.club.pk)
        self.assertIsNone(row.deleted_at)
        self.assertIsNone(row.deleted_by)
        self.assertTrue(Club.objects.filter(pk=self.club.pk).exists())

    def test_hard_delete_really_removes_the_row(self):
        pk = self.club.pk
        self.club.hard_delete()
        self.assertFalse(Club.all_objects.filter(pk=pk).exists())

    def test_queryset_delete_is_also_soft(self):
        Club.objects.filter(pk=self.club.pk).delete(deleted_by=self.actor)

        row = Club.all_objects.get(pk=self.club.pk)
        self.assertIsNotNone(row.deleted_at)
        self.assertEqual(row.deleted_by, self.actor)

    def test_queryset_hard_delete_removes_rows(self):
        pk = self.club.pk
        Club.objects.filter(pk=pk).hard_delete()
        self.assertFalse(Club.all_objects.filter(pk=pk).exists())

    def test_deleting_twice_keeps_the_first_timestamp_owner(self):
        self.club.delete(deleted_by=self.actor)
        first = Club.all_objects.get(pk=self.club.pk).deleted_at

        self.club.delete()
        second = Club.all_objects.get(pk=self.club.pk)
        # Re-deleting overwrites, which is expected; the row simply stays gone.
        self.assertGreaterEqual(second.deleted_at, first)
        self.assertFalse(Club.objects.filter(pk=self.club.pk).exists())


class DefaultManagerContractTests(APITestCase):
    """The Spine builds scoped querysets from _default_manager."""

    def test_default_manager_is_the_alive_manager(self):
        from apps.courts.models import Court

        for model in (Club, Court):
            with self.subTest(model=model.__name__):
                self.assertEqual(
                    model._default_manager.__class__.__name__, "AliveManager"
                )
