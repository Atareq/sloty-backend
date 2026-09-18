"""Shared abstract base models.

Two layers, deliberately separate:

``TimeStampedModel``
    ``created_at`` / ``modified_at``. Applied to every model in the project.

``SoftDeleteModel``
    Adds ``deleted_at`` / ``deleted_by`` and the three managers. Applied **only**
    to models where deletion is a real operation.

Soft delete is opt-in rather than universal because several domains forbid
deletion outright, and those rules are enforced by tests:

- ``apps/transactions`` is an immutable ledger — corrections are a logged cancel
  (``is_cancelled``), never a delete.
- ``apps/audit`` is append-only.
- ``apps/players`` states that ``ClubPlayer`` rows are never deleted.
- ``apps/bookings`` expresses lifecycle through statuses (``CANCELLED`` /
  ``EXPIRED``), not deletion.

Giving those models a ``delete()`` that silently succeeds would contradict the
domain, so they get timestamps only.

**Manager ordering matters.** ``objects`` is declared first, so it is
``_default_manager``. ``apps/common/authorization`` builds every authorized
queryset from ``model._default_manager``, which means soft-deleted rows drop out
of scoped querysets automatically and read as 404 rather than 403. That is
intended, and ``tests/authorization`` pins it.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone


class SoftDeleteQuerySet(models.QuerySet):
    """QuerySet understanding the alive/deleted split."""

    def alive(self):
        return self.filter(deleted_at__isnull=True)

    def dead(self):
        return self.filter(deleted_at__isnull=False)

    def delete(self, deleted_by=None):
        """Soft delete every row in the queryset.

        Overriding ``delete()`` means an accidental bulk ``.delete()`` marks rows
        instead of destroying them. Use ``hard_delete()`` when rows must really
        go.
        """
        return self.update(deleted_at=timezone.now(), deleted_by=deleted_by)

    def hard_delete(self):
        """Permanently remove the rows. Irreversible."""
        return super().delete()

    def restore(self):
        return self.update(deleted_at=None, deleted_by=None)


class AliveManager(models.Manager.from_queryset(SoftDeleteQuerySet)):
    """Default manager: hides soft-deleted rows."""

    def get_queryset(self):
        return super().get_queryset().alive()


class AllObjectsManager(models.Manager.from_queryset(SoftDeleteQuerySet)):
    """Everything, deleted rows included."""


class DeletedObjectsManager(models.Manager.from_queryset(SoftDeleteQuerySet)):
    """Only soft-deleted rows — the recycle bin view."""

    def get_queryset(self):
        return super().get_queryset().dead()


class TimeStampedModel(models.Model):
    """``created_at`` / ``modified_at`` for every model in the project."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    modified_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SoftDeleteModel(TimeStampedModel):
    """Timestamps plus reversible deletion.

    Only for models where deleting is a legitimate business operation. See the
    module docstring for why it is not applied everywhere.
    """

    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Who removed this row. Null for system or pre-existing deletions.",
    )

    # Declaration order is load-bearing: the first manager becomes
    # _default_manager, which the Authorization Spine builds every scoped
    # queryset from.
    objects = AliveManager()
    all_objects = AllObjectsManager()
    deleted_objects = DeletedObjectsManager()

    class Meta:
        abstract = True

    @property
    def is_deleted(self):
        return self.deleted_at is not None

    def delete(self, using=None, keep_parents=False, deleted_by=None):
        """Soft delete this row.

        Signature keeps Django's positional arguments so cascades and admin
        actions still work; they are accepted and ignored because nothing is
        actually removed.
        """
        self.deleted_at = timezone.now()
        self.deleted_by = deleted_by
        self.save(update_fields=["deleted_at", "deleted_by", "modified_at"])
        return (0, {})

    def hard_delete(self, using=None, keep_parents=False):
        """Permanently remove this row. Irreversible."""
        return super().delete(using=using, keep_parents=keep_parents)

    def restore(self):
        self.deleted_at = None
        self.deleted_by = None
        self.save(update_fields=["deleted_at", "deleted_by", "modified_at"])
