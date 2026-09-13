"""
ClubScopedViewMixin.

DRF ViewSet mixin hooking into the request lifecycle to resolve
RequestAccessContext immediately after authentication and prior to
permission evaluation.

SlotyScopedResourceMixin extends that lifecycle integration with the generic,
model-driven Authorization Spine v2 queryset contract.
"""

from django.core.exceptions import ImproperlyConfigured

from apps.common.authorization.contracts import load_authorization_config
from apps.common.authorization.querysets import scoped_queryset
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


class SlotyScopedResourceMixin(ClubScopedViewMixin):
    """
    Compose model-driven authorization scoping with an ordinary DRF ViewSet.

    Domain ViewSets may narrow the secured queryset through
    ``filter_scoped_queryset`` and may add relations through the two
    ``authorization_*_related`` attributes. They must not replace
    ``get_queryset`` with a model manager.
    """

    authorization_model = None
    authorization_scope = None
    authorization_select_related = ()
    authorization_prefetch_related = ()

    def get_access_context(self):
        context = self.access_context
        if context is None:
            self.resolve_access_context(self.request)
            context = self.access_context
        if context is None:
            raise ImproperlyConfigured(
                "SlotyScopedResourceMixin requires a resolved access context."
            )
        return context

    def get_authorization_model(self):
        if self.authorization_model is not None:
            return self.authorization_model
        declared_queryset = getattr(self, "queryset", None)
        model = getattr(declared_queryset, "model", None)
        if model is None:
            raise ImproperlyConfigured(
                f"{self.__class__.__name__} must declare authorization_model "
                "or a model queryset."
            )
        return model

    def get_resource_scope(self):
        if self.authorization_scope is not None:
            return self.authorization_scope
        return load_authorization_config(self.get_authorization_model()).default_scope

    def get_authorization_select_related(self):
        return self.authorization_select_related

    def get_authorization_prefetch_related(self):
        return self.authorization_prefetch_related

    def get_scoped_queryset(self):
        return scoped_queryset(
            self.get_access_context(),
            self.get_authorization_model(),
            scope=self.get_resource_scope(),
            extra_select_related=self.get_authorization_select_related(),
            extra_prefetch_related=self.get_authorization_prefetch_related(),
        )

    def filter_scoped_queryset(self, queryset):
        """Domain hook for narrowing an already-authorized queryset."""
        return queryset

    def get_queryset(self):
        model = self.get_authorization_model()
        if getattr(self, "swagger_fake_view", False):
            return model._default_manager.none()
        return self.filter_scoped_queryset(self.get_scoped_queryset())
