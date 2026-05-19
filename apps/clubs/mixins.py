from apps.clubs.access import ClubAccessContext


class ClubScopedAccessMixin:
    club_lookup_url_kwarg = "club_slug"

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
