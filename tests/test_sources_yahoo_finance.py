"""Tests for the yahoo_finance source adapter.

Fixtures under tests/fixtures/yahoo_finance/{ionq,rgti,qbts}_news.json are
real `yf.Ticker(<symbol>).news` responses, captured live with yfinance
1.7.0 on 2026-09-02 for IONQ, RGTI, and QBTS (the quantum topic's ticker
universe, config/tickers/quantum.yaml). They're kept verbatim, including
the cross-ticker overlap that showed up naturally in the real data (the
same Rigetti and NVIDIA-adjacent stories appear in more than one ticker's
feed) -- that overlap is exactly what TestFetchDedupeAcrossTickers below
exercises. All three came back in the newer "fields nested under content"
shape; none of yfinance's yf.Ticker/.news classic flat shape.

edge_cases.json is NOT captured data. None of the three real captures
happened to contain a malformed item, so this fixture is hand-built to
exercise the defensive-parsing paths a real, healthy response doesn't
naturally cover: a missing title, a missing URL, a missing publish time,
and the older flat-dict .news shape (no "content" key) that yfinance used
before it started mirroring Yahoo's newer content API.

No test in this module makes a network call; yf.Ticker is monkeypatched to
return fixture data (or raise, for the one-ticker-fails case) instead.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from topic_digest.config import ConfigError
from topic_digest.sources import build
from topic_digest.sources.yahoo_finance import YahooFinanceSource

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "yahoo_finance"

IONQ = {"symbol": "IONQ", "name": "IonQ"}
RGTI = {"symbol": "RGTI", "name": "Rigetti Computing"}
QBTS = {"symbol": "QBTS", "name": "D-Wave Quantum"}

FAR_PAST = datetime(2000, 1, 1, tzinfo=UTC)


def _load_fixture(name: str) -> list[dict]:
    return json.loads((FIXTURES_DIR / name).read_text())


def _options(tickers: list[dict]) -> dict:
    return {"tickers": tickers}


class _FakeTicker:
    """Stand-in for yf.Ticker: serves fixture data per symbol, or raises."""

    def __init__(self, symbol: str, news_by_symbol: dict, raise_for: tuple) -> None:
        self._symbol = symbol
        self._news_by_symbol = news_by_symbol
        self._raise_for = raise_for

    @property
    def news(self):
        if self._symbol in self._raise_for:
            raise RuntimeError(f"simulated yfinance failure for {self._symbol}")
        return self._news_by_symbol.get(self._symbol, [])


def _patch_yfinance(monkeypatch, news_by_symbol: dict, raise_for: tuple = ()) -> None:
    monkeypatch.setattr(
        "topic_digest.sources.yahoo_finance.yf.Ticker",
        lambda symbol: _FakeTicker(symbol, news_by_symbol, raise_for),
    )


class TestConstruction:
    def test_builds_from_inline_tickers(self, tmp_path):
        source = YahooFinanceSource(_options([IONQ]), tmp_path)
        assert isinstance(source, YahooFinanceSource)

    def test_raises_config_error_when_no_enabled_tickers(self, tmp_path):
        with pytest.raises(ConfigError):
            YahooFinanceSource(_options([{**IONQ, "enabled": False}]), tmp_path)

    def test_raises_config_error_when_ticker_list_is_empty(self, tmp_path):
        with pytest.raises(ConfigError):
            YahooFinanceSource(_options([]), tmp_path)

    def test_registered_under_yahoo_finance(self, tmp_path):
        source = build("yahoo_finance", _options([IONQ]), tmp_path)
        assert isinstance(source, YahooFinanceSource)


class TestFetchFieldMapping:
    def test_populates_title_url_publisher_snippet_and_time_from_real_response(
        self, tmp_path, monkeypatch
    ):
        _patch_yfinance(monkeypatch, {"IONQ": _load_fixture("ionq_news.json")})
        source = YahooFinanceSource(_options([IONQ]), tmp_path)

        articles = source.fetch(FAR_PAST)

        top = next(
            a
            for a in articles
            if a.url == "https://www.fool.com/investing/2026/09/02/where-will-ionq-stock-be-in-1-year/"
        )
        assert top.title == "Where Will IonQ Stock Be in 1 Year?"
        assert top.publisher == "Motley Fool"
        assert top.snippet == (
            "There are a lot of moving parts at IonQ, but it might take a "
            "while for the stock to gain new traction."
        )
        assert top.published_at == datetime(2026, 9, 2, 20, 20, tzinfo=UTC)

    def test_body_is_left_empty(self, tmp_path, monkeypatch):
        """Fetching full article text is a separate, source-agnostic concern
        (see the redirect this task took); this adapter only ever normalizes
        what .news itself hands back, which never includes body text.
        """
        _patch_yfinance(monkeypatch, {"IONQ": _load_fixture("ionq_news.json")})
        source = YahooFinanceSource(_options([IONQ]), tmp_path)

        articles = source.fetch(FAR_PAST)

        assert articles
        assert all(a.body == "" for a in articles)

    def test_published_at_is_timezone_aware_utc(self, tmp_path, monkeypatch):
        _patch_yfinance(monkeypatch, {"IONQ": _load_fixture("ionq_news.json")})
        source = YahooFinanceSource(_options([IONQ]), tmp_path)

        articles = source.fetch(FAR_PAST)

        assert all(a.published_at.tzinfo is not None for a in articles)
        assert all(a.published_at.utcoffset().total_seconds() == 0 for a in articles)

    def test_tags_hold_bare_symbols_with_display_name_in_ticker_names(self, tmp_path, monkeypatch):
        _patch_yfinance(monkeypatch, {"IONQ": _load_fixture("ionq_news.json")})
        source = YahooFinanceSource(_options([IONQ]), tmp_path)

        articles = source.fetch(FAR_PAST)

        assert articles
        assert all(a.tags == ("IONQ",) for a in articles)
        assert all(a.ticker_names == {"IONQ": "IonQ"} for a in articles)


class TestFetchSinceCutoff:
    def test_excludes_articles_published_before_since(self, tmp_path, monkeypatch):
        _patch_yfinance(monkeypatch, {"IONQ": _load_fixture("ionq_news.json")})
        source = YahooFinanceSource(_options([IONQ]), tmp_path)

        since = datetime(2026, 9, 1, tzinfo=UTC)
        articles = source.fetch(since)

        assert len(articles) == 5
        assert all(a.published_at >= since for a in articles)

    def test_includes_article_published_exactly_at_since(self, tmp_path, monkeypatch):
        _patch_yfinance(monkeypatch, {"IONQ": _load_fixture("ionq_news.json")})
        source = YahooFinanceSource(_options([IONQ]), tmp_path)

        since = datetime(2026, 9, 2, 20, 20, tzinfo=UTC)
        articles = source.fetch(since)

        assert len(articles) == 1
        assert articles[0].published_at == since


class TestFetchDedupeAcrossTickers:
    def test_shared_article_appears_once_with_tags_from_every_matching_ticker(
        self, tmp_path, monkeypatch
    ):
        _patch_yfinance(
            monkeypatch,
            {
                "IONQ": _load_fixture("ionq_news.json"),
                "RGTI": _load_fixture("rgti_news.json"),
            },
        )
        source = YahooFinanceSource(_options([IONQ, RGTI]), tmp_path)

        shared_url = (
            "https://finance.yahoo.com/technology/ai/articles/"
            "rigetti-expands-quantum-footprint-1-145900711.html"
        )
        articles = source.fetch(FAR_PAST)
        matches = [a for a in articles if a.url == shared_url]

        assert len(matches) == 1
        assert matches[0].tags == ("IONQ", "RGTI")
        assert matches[0].ticker_names == {"IONQ": "IonQ", "RGTI": "Rigetti Computing"}

    def test_total_count_reflects_dedupe_not_sum_of_raw_items(self, tmp_path, monkeypatch):
        ionq_raw = _load_fixture("ionq_news.json")
        rgti_raw = _load_fixture("rgti_news.json")
        _patch_yfinance(monkeypatch, {"IONQ": ionq_raw, "RGTI": rgti_raw})
        source = YahooFinanceSource(_options([IONQ, RGTI]), tmp_path)

        articles = source.fetch(FAR_PAST)
        urls = [a.url for a in articles]

        assert len(urls) == len(set(urls))
        assert len(articles) < len(ionq_raw) + len(rgti_raw)


class TestFetchOneTickerFailing:
    def test_one_ticker_raising_does_not_fail_the_whole_batch(self, tmp_path, monkeypatch, caplog):
        _patch_yfinance(
            monkeypatch,
            {
                "IONQ": _load_fixture("ionq_news.json"),
                "QBTS": _load_fixture("qbts_news.json"),
            },
            raise_for=("RGTI",),
        )
        source = YahooFinanceSource(_options([IONQ, RGTI, QBTS]), tmp_path)

        with caplog.at_level("WARNING"):
            articles = source.fetch(FAR_PAST)

        assert articles
        assert any("RGTI" in a.tags for a in articles) is False
        assert any(
            record.levelname == "WARNING" and "RGTI" in record.getMessage()
            for record in caplog.records
        )

    def test_all_tickers_raising_returns_empty_list_not_an_exception(self, tmp_path, monkeypatch):
        _patch_yfinance(monkeypatch, {}, raise_for=("IONQ",))
        source = YahooFinanceSource(_options([IONQ]), tmp_path)

        assert source.fetch(FAR_PAST) == []


class TestFetchMalformedItems:
    def test_skips_item_missing_title_url_or_timestamp_without_raising(self, tmp_path, monkeypatch):
        _patch_yfinance(monkeypatch, {"IONQ": _load_fixture("edge_cases.json")})
        source = YahooFinanceSource(_options([IONQ]), tmp_path)

        articles = source.fetch(FAR_PAST)

        # Of the four edge_cases.json entries, only the flat-shape one is
        # complete enough to normalize; the other three are each missing
        # exactly one required field and must be skipped, not raise.
        assert len(articles) == 1

    def test_parses_the_older_flat_dict_shape_without_a_content_key(self, tmp_path, monkeypatch):
        _patch_yfinance(monkeypatch, {"IONQ": _load_fixture("edge_cases.json")})
        source = YahooFinanceSource(_options([IONQ]), tmp_path)

        articles = source.fetch(FAR_PAST)

        assert len(articles) == 1
        article = articles[0]
        assert article.title == "IonQ Announces New Trapped-Ion Milestone"
        assert article.url == "https://example.invalid/articles/ionq-milestone"
        assert article.publisher == "Example Wire"
        assert article.published_at == datetime(2026, 1, 1, tzinfo=UTC)
