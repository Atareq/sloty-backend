from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from apps.clubs.access import ClubAccessContext
from apps.clubs.models import ClubMembership


class ClubScopedAccessMixin:
    club_lookup_url_kwarg = "club_slug"
    last_sync_update_interval = timedelta(minutes=5)

    def get_access_context(self):
        if not hasattr(self, "_club_access_context"):
            self._club_access_context = ClubAccessContext.from_request(
                request=self.request,
                club_slug=self.kwargs[self.club_lookup_url_kwarg],
            )
        return self._club_access_context

    def get_club(self):
        return self.get_access_context().club

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if not getattr(self, "swagger_fake_view", False):
            context["club_access"] = self.get_access_context()
        return context

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        self.update_last_sync_at(response)
        return response

    def update_last_sync_at(self, response):
        if not self.should_update_last_sync_at(response):
            return

        access = self._club_access_context
        now = timezone.now()
        stale_before = now - self.last_sync_update_interval
        ClubMembership.objects.granting_access().filter(
            club=access.club,
            user=access.user,
        ).filter(
            Q(last_sync_at__isnull=True) | Q(last_sync_at__lt=stale_before)
        ).update(
            last_sync_at=now
        )

    def should_update_last_sync_at(self, response):
        request = getattr(self, "request", None)
        user = getattr(request, "user", None)
        return (
            response is not None
            and 200 <= response.status_code < 400
            and user is not None
            and user.is_authenticated
            and hasattr(self, "_club_access_context")
            and not self._club_access_context.is_platform_admin
        )
