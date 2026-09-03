"""Yahoo Finance news source adapter for ticker-tracking topics.

yfinance is an unofficial client for an endpoint Yahoo has never published
or versioned as a stable API: it mirrors whatever finance.yahoo.com happens
to return today, scraped rather than served through a contract. That shape
has already changed under yfinance's feet once, from a flat dict of fields
to a dict with those same fields nested under a "content" key, and nothing
guarantees it won't change again. This module is the only place in the
pipeline that shape lives, and the only place a scrape-shape change or a
single ticker's request failing gets absorbed instead of propagated:
everything downstream of fetch() only ever sees topic_digest.models.Article.

Full article body text is deliberately out of scope here. .news never
returns it (confirmed against real captures; see tests/fixtures/
yahoo_finance/), and fetching it from the article's own URL is a
source-agnostic concern that belongs in its own pipeline stage, not
copy-pasted into every adapter that could use it.
"""

import logging
from datetime import UTC, datetime
from pathlib import Path

import yfinance as yf

from topic_digest.config import ConfigError, TickerEntry, load_tickers
from topic_digest.models import Article
from topic_digest.sources import register

logger = logging.getLogger(__name__)


def _unwrap(raw_item: dict) -> dict:
    """Unwrap yfinance's nested-content .news shape into a flat dict.

    Older yfinance returned .news items as a flat dict of fields; newer
    versions wrap those same fields under a "content" key. Both are handled
    here so a version bump doesn't require touching the parsing below.
    """
    content = raw_item.get("content")
    return content if isinstance(content, dict) else raw_item


def _extract_url(item: dict) -> str | None:
    for key in ("canonicalUrl", "clickThroughUrl"):
        candidate = item.get(key)
        if isinstance(candidate, dict) and candidate.get("url"):
            return candidate["url"]
    return item.get("link") or None


def _extract_published_at(item: dict) -> datetime | None:
    pub_date = item.get("pubDate")
    if isinstance(pub_date, str) and pub_date:
        try:
            parsed = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None:
            return parsed.astimezone(UTC)

    epoch = item.get("providerPublishTime")
    if isinstance(epoch, int | float):
        return datetime.fromtimestamp(epoch, tz=UTC)

    return None


def _extract_publisher(item: dict) -> str:
    provider = item.get("provider")
    if isinstance(provider, dict) and provider.get("displayName"):
        return provider["displayName"]
    return item.get("publisher") or ""


def _extract_snippet(item: dict) -> str:
    return item.get("summary") or item.get("description") or ""


@register("yahoo_finance")
class YahooFinanceSource:
    """Fetches recent news for a topic's ticker universe via yfinance.

    There is no watchlist concept here: every enabled ticker in the
    resolved universe (see topic_digest.config.load_tickers) is fetched and
    treated identically. `config_dir` is needed for the same reason
    load_tickers needs it: a `tickers_file` path in `options` is relative to
    the topic config file's own directory, not wherever the process
    happens to be running from.

    Each returned Article's `tags` holds the bare symbol(s) it matched
    (e.g. "IONQ"), with the display name for each ("IonQ") available via
    `ticker_names`, since articles refer to companies by name, never by
    bare symbol, and a Slack reader shouldn't have to know the difference.
    """

    def __init__(self, options: dict, config_dir: str | Path) -> None:
        self._tickers: list[TickerEntry] = load_tickers(options, config_dir)
        if not self._tickers:
            raise ConfigError(
                "yahoo_finance source has no enabled tickers; check "
                "source.options.tickers / tickers_file"
            )

    def fetch(self, since: datetime) -> list[Article]:
        articles: dict[str, Article] = {}
        symbols_by_fingerprint: dict[str, list[str]] = {}
        ticker_names: dict[str, str] = {}

        for ticker in self._tickers:
            try:
                raw_items = yf.Ticker(ticker.symbol).news
            except Exception:
                logger.warning(
                    "Failed to fetch news for %s (%s); skipping this ticker",
                    ticker.symbol,
                    ticker.name,
                    exc_info=True,
                )
                continue

            ticker_names[ticker.symbol] = ticker.name

            for raw_item in raw_items or []:
                item = _unwrap(raw_item)

                title = item.get("title")
                url = _extract_url(item)
                published_at = _extract_published_at(item)
                if not title or not url or published_at is None:
                    logger.debug(
                        "Skipping malformed news item for %s: %r", ticker.symbol, raw_item
                    )
                    continue

                if published_at < since:
                    continue

                article = Article(
                    title=title,
                    url=url,
                    published_at=published_at,
                    publisher=_extract_publisher(item),
                    snippet=_extract_snippet(item),
                    body="",
                )
                fingerprint = article.fingerprint

                if fingerprint not in articles:
                    articles[fingerprint] = article
                    symbols_by_fingerprint[fingerprint] = []

                symbols = symbols_by_fingerprint[fingerprint]
                if ticker.symbol not in symbols:
                    symbols.append(ticker.symbol)

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
