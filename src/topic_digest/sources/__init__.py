"""Adapters that fetch raw articles for a topic, registered by name for config to select.

A topic's YAML names a source adapter (e.g. "rss", "arxiv", "newsapi") under
source.adapter, and hands it topic_digest.config.SourceSection.options, an
untyped dict. This package deliberately has no idea what a valid options
shape looks like for any given adapter: knowing about tickers, arXiv
categories, or any other provider-specific concept here would mean every new
source requires changing code that every other topic depends on. Each
adapter validates its own options and is the only thing that understands
them; this package only knows how to look one up by name and build it.

Every adapter is also handed the topic config file's own directory
(`config_dir`), whether or not it needs it. A ticker-tracking adapter needs
it to resolve a relative `tickers_file` the same way
topic_digest.config.load_tickers does; requiring it uniformly, rather than
inspecting each factory's signature to decide whether to pass it, keeps
`build()` simple and means an adapter can start ignoring or start using
`config_dir` without a registry change either way.
"""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from topic_digest.models import Article

_REGISTRY: dict[str, Callable[[dict, str | Path], "Source"]] = {}


@runtime_checkable
class Source(Protocol):
    """Anything that can fetch recent articles for a topic."""

    def fetch(self, since: datetime) -> list[Article]: ...


class UnknownAdapterError(Exception):
    """Raised when config names a source adapter that hasn't been registered."""


def register(
    name: str,
) -> Callable[[Callable[[dict, str | Path], Source]], Callable[[dict, str | Path], Source]]:
    """Register a Source factory (typically a class) under the adapter name used in config.

    The decorated object is called with a topic's `source.options` dict and
    the topic config file's directory, and must return a Source; a class
    whose __init__ takes `(options, config_dir)` satisfies this without any
    extra wrapping.
    """

    def decorator(
        factory: Callable[[dict, str | Path], Source],
    ) -> Callable[[dict, str | Path], Source]:
        if name in _REGISTRY:
            raise ValueError(f"Source adapter '{name}' is already registered")
        _REGISTRY[name] = factory
        return factory

    return decorator


def build(adapter: str, options: dict, config_dir: str | Path) -> Source:
    """Instantiate the adapter registered under `adapter`, passing it `options` to validate."""
    try:
        factory = _REGISTRY[adapter]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "(none registered)"
        raise UnknownAdapterError(
            f"Unknown source adapter '{adapter}'. Known adapters: {known}"
        ) from None
    return factory(options, config_dir)
