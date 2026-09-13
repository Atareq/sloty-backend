"""Fail-closed, model-driven queryset scoping for Authorization Spine v2."""

from collections.abc import Sequence
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.db.models import QuerySet

from apps.common.authorization.contracts import (
    SELF_SCOPE_PATH,
    load_authorization_config,
)
from apps.common.authorization.roles import Role
from apps.common.authorization.scopes import ResourceScope, normalize_scope_key


def _is_valid_club_context(context) -> bool:
    user = getattr(context, "user", None)
    club = getattr(context, "club", None)
    role = getattr(context, "role", None)
    if not user or not getattr(user, "is_authenticated", False) or club is None:
        return False

    if getattr(context, "is_platform_admin", False):
        is_platform_admin = getattr(user, "is_platform_super_admin", None)
        return bool(
            role == Role.ADMIN and callable(is_platform_admin) and is_platform_admin()
        )

    membership = getattr(context, "membership", None)
    return bool(
        role in Role.CLUB_ROLES
        and membership is not None
        and getattr(membership, "role", None) == role
        and getattr(membership, "club_id", None) == club.pk
        and getattr(membership, "user_id", None) == user.pk
        and getattr(membership, "is_active", False)
        and getattr(membership, "deleted_at", None) is None
    )


def _authorized_staff_court_ids(context) -> tuple[Any, ...]:
    court_ids = tuple(getattr(context, "court_ids", ()) or ())
    if court_ids:
        return court_ids
    membership = getattr(context, "membership", None)
    court_id = getattr(membership, "court_id", None)
    return (court_id,) if court_id is not None else ()


def _require_scope_value(queryset, path: str) -> QuerySet:
    if path == SELF_SCOPE_PATH:
        return queryset
    return queryset.filter(**{f"{path}__isnull": False})


def _filter_scope_value(queryset, *, path: str, value) -> QuerySet:
    if path == SELF_SCOPE_PATH:
        return queryset.filter(pk=getattr(value, "pk", value))
    return queryset.filter(**{path: value})


def _filter_scope_ids(queryset, *, path: str, values) -> QuerySet:
    lookup = "pk__in" if path == SELF_SCOPE_PATH else f"{path}__pk__in"
    return queryset.filter(**{lookup: values})


def _apply_court_scope(queryset, *, context, path: str) -> QuerySet:
    queryset = _require_scope_value(queryset, path)
    court = getattr(context, "court", None)
    if court is not None:
        if getattr(court, "club_id", None) != context.club.pk:
            return queryset.none()
        if context.role == Role.STAFF:
            if court.pk not in _authorized_staff_court_ids(context):
                return queryset.none()
        return _filter_scope_value(queryset, path=path, value=court)

    if context.role == Role.STAFF:
        court_ids = _authorized_staff_court_ids(context)
        if not court_ids:
            return queryset.none()
        return _filter_scope_ids(queryset, path=path, values=court_ids)

    if getattr(context, "is_platform_admin", False) or context.role in {
        Role.OWNER,
        Role.MANAGER,
    }:
        return queryset
    return queryset.none()


def _apply_future_scope(queryset, *, context, scope_key: str, path: str) -> QuerySet:
    """Apply a future scope from facts exposed on RequestAccessContext."""
    queryset = _require_scope_value(queryset, path)
    boundary = getattr(context, scope_key, None)
    if boundary is not None:
        return _filter_scope_value(queryset, path=path, value=boundary)

    boundary_ids = tuple(getattr(context, f"{scope_key}_ids", ()) or ())
    if boundary_ids:
        return _filter_scope_ids(queryset, path=path, values=boundary_ids)
    return queryset.none()


def _normalize_extra_relations(value, *, name: str) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ImproperlyConfigured(f"{name} must be a sequence.")
    return tuple(value)


def _prefetch_key(value):
    if isinstance(value, str):
        return value
    return getattr(value, "prefetch_to", id(value))


def _unique_relations(relations, *, key=lambda value: value):
    seen = set()
    unique = []
    for relation in relations:
        relation_key = key(relation)
        if relation_key not in seen:
            seen.add(relation_key)
            unique.append(relation)
    return tuple(unique)


def scoped_queryset(
    access_context,
    model,
    scope=None,
    *,
    extra_select_related=(),
    extra_prefetch_related=(),
) -> QuerySet:
    """
    Build an authorized queryset from model metadata and resolved context.

    The model manager is intentionally reconstructed here. Callers receive an
    already-secured queryset and may only narrow it afterwards.
    """
    config = load_authorization_config(model)
    try:
        scope_key = normalize_scope_key(
            config.default_scope if scope is None else scope
        )
    except ValueError as exc:
        raise ImproperlyConfigured(str(exc)) from exc

    queryset = model._default_manager.all()
    if scope_key == ResourceScope.NONE.value:
        return queryset.none()
    if scope_key not in config.scopes:
        raise ImproperlyConfigured(
            f"{model.__name__}.authorization_config does not declare scope "
            f"{scope_key!r}."
        )
    if not _is_valid_club_context(access_context):
        return queryset.none()

    club_scope = config.scopes.get(ResourceScope.CLUB.value)
    if club_scope is None:
        raise ImproperlyConfigured(
            f"{model.__name__}.authorization_config must declare a club scope."
        )
    queryset = _filter_scope_value(
        queryset,
        path=club_scope.path,
        value=access_context.club,
    )

    if scope_key == ResourceScope.COURT.value:
        queryset = _apply_court_scope(
            queryset,
            context=access_context,
            path=config.scopes[scope_key].path,
        )
    elif scope_key != ResourceScope.CLUB.value:
        queryset = _apply_future_scope(
            queryset,
            context=access_context,
            scope_key=scope_key,
            path=config.scopes[scope_key].path,
        )

    select_related = _unique_relations(
        config.select_related
        + _normalize_extra_relations(extra_select_related, name="extra_select_related")
    )
    prefetch_related = _unique_relations(
        config.prefetch_related
        + _normalize_extra_relations(
            extra_prefetch_related, name="extra_prefetch_related"
        ),
        key=_prefetch_key,
    )
    if select_related:
        queryset = queryset.select_related(*select_related)
    if prefetch_related:
        queryset = queryset.prefetch_related(*prefetch_related)
    return queryset
