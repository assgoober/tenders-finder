"""Agent registry.

Adding a portal must never require editing pipeline code. A new agent
subclasses `TenderSourceAgent`, decorates itself with `@register_agent`, and
gets imported in `app/agents/__init__.py`. Nothing else changes.
"""

from __future__ import annotations

import logging
from typing import Iterable, Type

from ..config import Settings, get_settings
from .base import TenderSourceAgent

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, Type[TenderSourceAgent]] = {}


def register_agent(cls: Type[TenderSourceAgent]) -> Type[TenderSourceAgent]:
    """Class decorator that adds an agent to the registry."""
    key = getattr(cls, "key", "")
    if not key:
        raise ValueError(f"{cls.__name__} must define a non-empty `key` to be registered")
    if key in _REGISTRY and _REGISTRY[key] is not cls:
        raise ValueError(
            f"duplicate agent key {key!r}: already registered to {_REGISTRY[key].__name__}"
        )
    _REGISTRY[key] = cls
    return cls


def available_keys() -> list[str]:
    return sorted(_REGISTRY)


def get_agent(key: str, settings: Settings | None = None) -> TenderSourceAgent:
    try:
        cls = _REGISTRY[key]
    except KeyError:
        raise KeyError(
            f"no agent registered for source {key!r}. Available: {', '.join(available_keys()) or '(none)'}"
        ) from None
    return cls(settings or get_settings())


def get_agents(
    keys: Iterable[str] | None = None, settings: Settings | None = None
) -> list[TenderSourceAgent]:
    settings = settings or get_settings()
    selected = list(keys) if keys is not None else available_keys()
    return [get_agent(key, settings) for key in selected]


def describe_agents() -> list[dict]:
    """Registry contents, for the /sources endpoint and for seeding
    `tender_sources` rows."""
    return [
        {
            "key": cls.key,
            "display_name": cls.display_name or cls.key,
            "base_url": cls.base_url,
            "country": cls.country,
            "sector": cls.sector,
        }
        for cls in sorted(_REGISTRY.values(), key=lambda c: c.key)
    ]
