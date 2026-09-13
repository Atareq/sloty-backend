"""
ClubScopedViewMixin.

DRF ViewSet mixin hooking into the request lifecycle to resolve
RequestAccessContext immediately after authentication and prior to
permission evaluation.
"""

from apps.common.authorization.resolver import resolve_club_scope, resolve_global_scope


class ClubScopedViewMixin:
    """
    Mixin for ViewSets operating within a club URL scope
    (/api/v1/clubs/{club_slug}/...).

    Integrates into DRF's lifecycle by resolving RequestAccessContext during
    perform_authentication(), ensuring context is fully established before
    SlotyBasePermission runs.
    """

    club_lookup_url_kwarg = "club_slug"
    court_lookup_url_kwarg = "court_id"

    def perform_authentication(self, request):
        super().perform_authentication(request)
        self.resolve_access_context(request)

    def resolve_access_context(self, request):
        club_slug = self.kwargs.get(self.club_lookup_url_kwarg)
        court_id = self.kwargs.get(self.court_lookup_url_kwarg)
        if club_slug:
            resolve_club_scope(request, club_slug=club_slug, court_id=court_id)
        elif getattr(request, "user", None) and request.user.is_authenticated:
            resolve_global_scope(request)

    @property
    def access_context(self):
        return getattr(self.request, "access_context", None)
