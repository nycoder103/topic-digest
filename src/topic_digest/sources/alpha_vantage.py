"""Alpha Vantage NEWS_SENTIMENT source adapter for ticker-tracking topics.

Alpha Vantage's NEWS_SENTIMENT is an official, versioned API (unlike
yahoo_finance's unofficial scrape of a page Yahoo has never published as a
stable contract), and it accepts an entire ticker universe in one request
rather than one call per symbol: `tickers` is a comma-joined list, and each
returned feed item already carries every matching symbol in its own
`ticker_sentiment` list. The dedup-by-fingerprint pass below exists anyway,
mirroring yahoo_finance.py's defensive posture, in case the same article
ever appears more than once in a single response.

Full article body text is out of scope here too: NEWS_SENTIMENT has no body
field, only `summary`, a short snippet. See tests/fixtures/alpha_vantage/
README.md for the fixture's provenance and the response shape this module
was written against.
"""

import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import requests

from topic_digest.config import ConfigError, TickerEntry, load_tickers
from topic_digest.models import Article
from topic_digest.sources import register

logger = logging.getLogger(__name__)

_NEWS_SENTIMENT_URL = "https://www.alphavantage.co/query"

# Environment variable names are conventionally SCREAMING_SNAKE_CASE; a
# literal API key pasted in by mistake (mixed case, random-looking) won't
# match this, which is the same "looks like a literal secret, not a
# variable name" check topic_digest.config.SlackConfig does for
# webhook_env via its startswith("https://") check.
_ENV_VAR_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def _parse_time_published(value: object) -> datetime | None:
    """Parse Alpha Vantage's `YYYYMMDDTHHMMSS` timestamp into a UTC datetime."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        return None


@register("alpha_vantage")
class AlphaVantageSource:
    """Fetches recent news for a topic's ticker universe via Alpha Vantage NEWS_SENTIMENT.

    `config_dir` is needed for the same reason load_tickers needs it: a
    `tickers_file` path in `options` is relative to the topic config file's
    own directory, not wherever the process happens to be running from.

    Each returned Article's `tags` holds the bare symbol(s), from this
    topic's ticker universe, that Alpha Vantage's own `ticker_sentiment`
    judged relevant to that story, with the display name for each available
    via `ticker_names`, matching yahoo_finance.py's convention.
    """

    def __init__(self, options: dict, config_dir: str | Path) -> None:
        api_key_env = options.get("api_key_env")
        if not api_key_env:
            raise ConfigError("alpha_vantage source.options must set 'api_key_env'")
        if not _ENV_VAR_NAME_RE.fullmatch(api_key_env):
            raise ConfigError(
                "source.options.api_key_env must name an environment variable "
                "(e.g. 'ALPHA_VANTAGE_API_KEY'), not an API key value itself. Got "
                f"{api_key_env!r}, which doesn't look like an environment variable "
                "name; put the key in an environment variable and reference that "
                "variable's name here instead."
            )
        self._api_key_env = api_key_env

        self._tickers: list[TickerEntry] = load_tickers(options, config_dir)
        if not self._tickers:
            raise ConfigError(
                "alpha_vantage source has no enabled tickers; check "
                "source.options.tickers / tickers_file"
            )

    def fetch(self, since: datetime) -> list[Article]:
        symbols = [ticker.symbol for ticker in self._tickers]
        universe = set(symbols)
        ticker_names = {ticker.symbol: ticker.name for ticker in self._tickers}

        params = {
            "function": "NEWS_SENTIMENT",
            "tickers": ",".join(symbols),
            "time_from": since.strftime("%Y%m%dT%H%M"),
            "apikey": os.environ[self._api_key_env],
        }
        response = requests.get(_NEWS_SENTIMENT_URL, params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()

        if "feed" not in payload:
            info = payload.get("Information") or payload.get("Note") or payload
            raise ConfigError(f"Alpha Vantage NEWS_SENTIMENT did not return a feed: {info}")

        articles: dict[str, Article] = {}
        symbols_by_fingerprint: dict[str, list[str]] = {}

        for item in payload["feed"]:
            title = item.get("title")
            url = item.get("url")
            published_at = _parse_time_published(item.get("time_published"))
            if not title or not url or published_at is None:
                logger.debug("Skipping malformed Alpha Vantage feed item: %r", item)
                continue

            if published_at < since:
                continue

            matched_symbols = [
                entry.get("ticker")
                for entry in item.get("ticker_sentiment", [])
                if entry.get("ticker") in universe
            ]

            article = Article(
                title=title,
                url=url,
                published_at=published_at,
                publisher=item.get("source") or "",
                snippet=item.get("summary") or "",
                body="",
            )
            fingerprint = article.fingerprint

            if fingerprint not in articles:
                articles[fingerprint] = article
                symbols_by_fingerprint[fingerprint] = []

            matched = symbols_by_fingerprint[fingerprint]
            for symbol in matched_symbols:
                if symbol not in matched:
                    matched.append(symbol)

        return [
            Article(
                title=article.title,
                url=article.url,
                published_at=article.published_at,
                publisher=article.publisher,
                snippet=article.snippet,
                body=article.body,
                tags=tuple(symbols_by_fingerprint[fingerprint]),
                ticker_names={
                    symbol: ticker_names[symbol] for symbol in symbols_by_fingerprint[fingerprint]
                },
            )
            for fingerprint, article in articles.items()
        ]
