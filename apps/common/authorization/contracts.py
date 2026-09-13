"""Validated model metadata contract for Authorization Spine v2."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from django.core.exceptions import ImproperlyConfigured

from apps.common.authorization.scopes import ResourceScope, normalize_scope_key

AUTHORIZATION_CONFIG_ATTRIBUTE = "authorization_config"
SELF_SCOPE_PATH = "self"


@dataclass(frozen=True)
class ResourceScopeDefinition:
    """ORM traversal, or ``self``, used to apply one authorization boundary."""

    path: str


@dataclass(frozen=True)
class ModelAuthorizationConfig:
    """Normalized, immutable representation of a model authorization contract."""

    scopes: Mapping[str, ResourceScopeDefinition]
    default_scope: str
    select_related: tuple[str, ...]
    prefetch_related: tuple[Any, ...]


def _configuration_error(model, message: str) -> ImproperlyConfigured:
    model_name = getattr(model, "__name__", repr(model))
    return ImproperlyConfigured(
        f"{model_name}.{AUTHORIZATION_CONFIG_ATTRIBUTE} {message}"
    )


def _normalize_select_related(model, value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _configuration_error(model, "select_related must be a sequence.")
    relations = tuple(value)
    if any(
        not isinstance(relation, str) or not relation.strip() for relation in relations
    ):
        raise _configuration_error(
            model, "select_related entries must be non-empty strings."
        )
    return relations


def _normalize_prefetch_related(model, value) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _configuration_error(model, "prefetch_related must be a sequence.")
    relations = tuple(value)
    for relation in relations:
        if isinstance(relation, str):
            if relation.strip():
                continue
        elif hasattr(relation, "prefetch_through"):
            continue
        raise _configuration_error(
            model,
            "prefetch_related entries must be non-empty strings or Prefetch objects.",
        )
    return relations


def load_authorization_config(model) -> ModelAuthorizationConfig:
    """
    Load and validate the single authorization configuration exposed by a model.

    Missing or malformed configuration raises instead of falling back to an
    unscoped manager, so an opt-in mistake cannot become a data-access bypass.
    """
    raw_config = getattr(model, AUTHORIZATION_CONFIG_ATTRIBUTE, None)
    if not isinstance(raw_config, Mapping):
        raise _configuration_error(model, "must be a mapping.")

    raw_scopes = raw_config.get("scopes")
    if not isinstance(raw_scopes, Mapping):
        raise _configuration_error(model, "scopes must be a mapping.")

    normalized_scopes = {}
    for raw_key, raw_definition in raw_scopes.items():
        try:
            key = normalize_scope_key(raw_key)
        except ValueError as exc:
            raise _configuration_error(model, str(exc)) from exc
        if key == ResourceScope.NONE.value:
            raise _configuration_error(
                model, "must not define an ORM path for the none scope."
            )
        if not isinstance(raw_definition, Mapping):
            raise _configuration_error(model, f"scope {key!r} must be a mapping.")
        path = raw_definition.get("path")
        if not isinstance(path, str) or not path.strip():
            raise _configuration_error(
                model, f"scope {key!r} must define a non-empty path."
            )
        normalized_scopes[key] = ResourceScopeDefinition(path=path.strip())

    try:
        default_scope = normalize_scope_key(raw_config.get("default_scope"))
    except ValueError as exc:
        raise _configuration_error(model, str(exc)) from exc

    if (
        default_scope != ResourceScope.NONE.value
        and default_scope not in normalized_scopes
    ):
        raise _configuration_error(
            model, f"default_scope {default_scope!r} is not declared in scopes."
        )

    if (
        default_scope != ResourceScope.NONE.value
        and ResourceScope.CLUB.value not in normalized_scopes
    ):
        raise _configuration_error(
            model, "must declare a club scope for every scoped resource."
        )

    return ModelAuthorizationConfig(
        scopes=MappingProxyType(normalized_scopes),
        default_scope=default_scope,
        select_related=_normalize_select_related(
            model, raw_config.get("select_related", ())
        ),
        prefetch_related=_normalize_prefetch_related(
            model, raw_config.get("prefetch_related", ())
        ),
    )
