"""Tests for the alpha_vantage source adapter.

`aapl_news_demo.json` would normally be a real `NEWS_SENTIMENT` response
captured live against Alpha Vantage's public, keyless demo endpoint,
mirroring the convention in tests/fixtures/yahoo_finance/. It isn't, this
time: see tests/fixtures/alpha_vantage/README.md for why the live capture
couldn't be made and what was built instead. It's shaped to match the
response format the adapter's spec describes.

`edge_cases.json` is hand-built either way, exercising a quota-exceeded
response and feed items each missing exactly one required field.

No test in this module makes a network call; requests.get is monkeypatched
to return fixture data instead.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from topic_digest.config import ConfigError
from topic_digest.sources import build
from topic_digest.sources.alpha_vantage import AlphaVantageSource

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "alpha_vantage"

IONQ = {"symbol": "IONQ", "name": "IonQ"}
RGTI = {"symbol": "RGTI", "name": "Rigetti Computing"}

FAR_PAST = datetime(2000, 1, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _default_api_key_env(monkeypatch):
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "fake-key-for-tests")


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text())


def _options(tickers: list[dict], api_key_env: str = "ALPHA_VANTAGE_API_KEY") -> dict:
    return {"tickers": tickers, "api_key_env": api_key_env}


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"simulated HTTP {self.status_code}")


def _patch_requests_get(monkeypatch, payload: dict, capture: dict | None = None):
    def fake_get(url, params=None, timeout=None):
        if capture is not None:
            capture["url"] = url
            capture["params"] = params
            capture["timeout"] = timeout
        return _FakeResponse(payload)

    monkeypatch.setattr("topic_digest.sources.alpha_vantage.requests.get", fake_get)


class TestConstruction:
    def test_builds_from_inline_tickers(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "fake-key")
        source = AlphaVantageSource(_options([IONQ]), tmp_path)
        assert isinstance(source, AlphaVantageSource)

    def test_raises_config_error_when_api_key_env_missing(self, tmp_path):
        options = {"tickers": [IONQ]}
        with pytest.raises(ConfigError, match="api_key_env"):
            AlphaVantageSource(options, tmp_path)

    def test_raises_config_error_when_api_key_env_looks_like_a_literal_key(self, tmp_path):
        with pytest.raises(ConfigError, match="environment variable"):
            AlphaVantageSource(_options([IONQ], api_key_env="my-literal-api-key-value"), tmp_path)

    def test_raises_config_error_when_no_enabled_tickers(self, tmp_path):
        with pytest.raises(ConfigError):
            AlphaVantageSource(_options([{**IONQ, "enabled": False}]), tmp_path)

    def test_registered_under_alpha_vantage(self, tmp_path):
        source = build("alpha_vantage", _options([IONQ]), tmp_path)
        assert isinstance(source, AlphaVantageSource)


class TestFetchFieldMapping:
    AAPL = {"symbol": "AAPL", "name": "Apple"}
    NVDA = {"symbol": "NVDA", "name": "NVIDIA"}

    def test_populates_title_url_publisher_snippet_and_time_from_fixture_response(
        self, tmp_path, monkeypatch
    ):
        _patch_requests_get(monkeypatch, _load_fixture("aapl_news_demo.json"))
        source = AlphaVantageSource(_options([self.AAPL]), tmp_path)

        articles = source.fetch(FAR_PAST)

        top = next(
            a
            for a in articles
            if a.url == "https://example.invalid/articles/apple-next-gen-chip-lineup"
        )
        assert top.title == "Apple Unveils Next-Generation Chip Lineup Ahead of Holiday Quarter"
        assert top.publisher == "Example Newswire"
        assert top.snippet.startswith("Apple detailed its latest chip lineup")
        assert top.published_at == datetime(2026, 10, 18, 11, 0, 5, tzinfo=UTC)

    def test_body_is_left_empty(self, tmp_path, monkeypatch):
        _patch_requests_get(monkeypatch, _load_fixture("aapl_news_demo.json"))
        source = AlphaVantageSource(_options([self.AAPL]), tmp_path)

        articles = source.fetch(FAR_PAST)

        assert articles
        assert all(a.body == "" for a in articles)

    def test_published_at_is_timezone_aware_utc(self, tmp_path, monkeypatch):
        _patch_requests_get(monkeypatch, _load_fixture("aapl_news_demo.json"))
        source = AlphaVantageSource(_options([self.AAPL]), tmp_path)

        articles = source.fetch(FAR_PAST)

        assert all(a.published_at.tzinfo is not None for a in articles)
        assert all(a.published_at.utcoffset().total_seconds() == 0 for a in articles)

    def test_tags_hold_only_symbols_in_this_topics_universe(self, tmp_path, monkeypatch):
        """The roundup story's ticker_sentiment includes NVDA, which isn't in
        this topic's universe (only AAPL is configured); tags must not
        include it.
        """
        _patch_requests_get(monkeypatch, _load_fixture("aapl_news_demo.json"))
        source = AlphaVantageSource(_options([self.AAPL]), tmp_path)

        articles = source.fetch(FAR_PAST)
        roundup = next(a for a in articles if "roundup" in a.url)

        assert roundup.tags == ("AAPL",)
        assert roundup.ticker_names == {"AAPL": "Apple"}

    def test_tags_hold_every_matching_symbol_when_both_are_tracked(self, tmp_path, monkeypatch):
        _patch_requests_get(monkeypatch, _load_fixture("aapl_news_demo.json"))
        source = AlphaVantageSource(_options([self.AAPL, self.NVDA]), tmp_path)

        articles = source.fetch(FAR_PAST)
        roundup = next(a for a in articles if "roundup" in a.url)

        assert set(roundup.tags) == {"AAPL", "NVDA"}
        assert roundup.ticker_names == {"AAPL": "Apple", "NVDA": "NVIDIA"}


class TestFetchSinceCutoff:
    AAPL = {"symbol": "AAPL", "name": "Apple"}

    def test_excludes_articles_published_before_since(self, tmp_path, monkeypatch):
        _patch_requests_get(monkeypatch, _load_fixture("aapl_news_demo.json"))
        source = AlphaVantageSource(_options([self.AAPL]), tmp_path)

        since = datetime(2026, 10, 17, tzinfo=UTC)
        articles = source.fetch(since)

        assert len(articles) == 2
        assert all(a.published_at >= since for a in articles)


class TestFetchRequestShape:
    AAPL = {"symbol": "AAPL", "name": "Apple"}

    def test_request_params_include_function_tickers_time_from_and_key_from_env(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "super-secret-value")
        capture: dict = {}
        _patch_requests_get(monkeypatch, _load_fixture("aapl_news_demo.json"), capture=capture)
        nvda = {"symbol": "NVDA", "name": "NVIDIA"}
        source = AlphaVantageSource(_options([self.AAPL, nvda]), tmp_path)

        since = datetime(2026, 10, 1, 9, 30, tzinfo=UTC)
        source.fetch(since)

        assert capture["url"] == "https://www.alphavantage.co/query"
        params = capture["params"]
        assert params["function"] == "NEWS_SENTIMENT"
        assert params["tickers"] == "AAPL,NVDA"
        assert params["time_from"] == "20261001T0930"
        assert params["apikey"] == "super-secret-value"

    def test_api_key_is_read_from_environment_at_call_time_not_construction_time(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "key-at-construction")
        capture: dict = {}
        _patch_requests_get(monkeypatch, _load_fixture("aapl_news_demo.json"), capture=capture)
        source = AlphaVantageSource(_options([self.AAPL]), tmp_path)

        monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "key-at-fetch-time")
        source.fetch(FAR_PAST)

        assert capture["params"]["apikey"] == "key-at-fetch-time"


class TestFetchErrorResponse:
    AAPL = {"symbol": "AAPL", "name": "Apple"}

    def test_quota_exceeded_response_raises_config_error_not_key_error(self, tmp_path, monkeypatch):
        payload = _load_fixture("edge_cases.json")["quota_exceeded_response"]
        _patch_requests_get(monkeypatch, payload)
        source = AlphaVantageSource(_options([self.AAPL]), tmp_path)

        with pytest.raises(ConfigError, match="rate limit"):
            source.fetch(FAR_PAST)


class TestFetchMalformedItems:
    IONQ = {"symbol": "IONQ", "name": "IonQ"}

    def test_skips_items_missing_title_url_or_parseable_time_without_raising(
        self, tmp_path, monkeypatch
    ):
        payload = _load_fixture("edge_cases.json")["feed_with_malformed_items"]
        _patch_requests_get(monkeypatch, payload)
        source = AlphaVantageSource(_options([self.IONQ]), tmp_path)

        articles = source.fetch(FAR_PAST)

        assert articles == []
