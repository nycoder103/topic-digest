"""Adapters that fetch raw articles for a topic, registered by name for config to select.

A topic's YAML names a source adapter (e.g. "rss", "arxiv", "newsapi") under
source.adapter, and hands it topic_digest.config.SourceSection.options, an
untyped dict. This package deliberately has no idea what a valid options
shape looks like for any given adapter: knowing about tickers, arXiv
categories, or any other provider-specific concept here would mean every new
source requires changing code that every other topic depends on. Each
adapter validates its own options and is the only thing that understands
them; this package only knows how to look one up by name and build it.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Protocol, runtime_checkable

from topic_digest.models import Article

_REGISTRY: dict[str, Callable[[dict], "Source"]] = {}


@runtime_checkable
class Source(Protocol):
    """Anything that can fetch recent articles for a topic."""

    def fetch(self, since: datetime) -> list[Article]: ...


class UnknownAdapterError(Exception):
    """Raised when config names a source adapter that hasn't been registered."""


def register(name: str) -> Callable[[Callable[[dict], Source]], Callable[[dict], Source]]:
    """Register a Source factory (typically a class) under the adapter name used in config.

    The decorated object is called with a topic's `source.options` dict and
    must return a Source; a class whose __init__ takes `options` satisfies
    this without any extra wrapping.
    """

    def decorator(factory: Callable[[dict], Source]) -> Callable[[dict], Source]:
        if name in _REGISTRY:
            raise ValueError(f"Source adapter '{name}' is already registered")
        _REGISTRY[name] = factory
        return factory

    return decorator


def build(adapter: str, options: dict) -> Source:
    """Instantiate the adapter registered under `adapter`, passing it `options` to validate."""
    try:
        factory = _REGISTRY[adapter]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "(none registered)"
        raise UnknownAdapterError(
            f"Unknown source adapter '{adapter}'. Known adapters: {known}"
        ) from None
    return factory(options)
