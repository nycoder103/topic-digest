"""Tests for the pipeline's shared data model.

These pin down the two behaviors every stage of the pipeline relies on:
that the same article arriving under different URLs still dedupes to one
fingerprint, and that summarization always has *something* to read even
when full article bodies can't be fetched.
"""

from datetime import UTC, datetime

from topic_digest.models import Article, canonical_url


def _article(**overrides):
    defaults = dict(
        title="Example Article",
        url="https://example.com/posts/some-article",
        published_at=datetime(2026, 8, 24, tzinfo=UTC),
        publisher="Example Publisher",
        snippet="A short snippet.",
        body="",
        tags=(),
    )
    defaults.update(overrides)
    return Article(**defaults)


class TestCanonicalUrl:
    def test_strips_query_string(self):
        url = "https://example.com/posts/some-article?utm_source=newsletter&utm_medium=email"
        assert canonical_url(url) == "https://example.com/posts/some-article"

    def test_strips_fragment(self):
        url = "https://example.com/posts/some-article#section-2"
        assert canonical_url(url) == "https://example.com/posts/some-article"

    def test_strips_trailing_slash(self):
        url = "https://example.com/posts/some-article/"
        assert canonical_url(url) == "https://example.com/posts/some-article"

    def test_strips_query_fragment_and_trailing_slash_together(self):
        url = "https://example.com/posts/some-article/?utm_source=digest#top"
        assert canonical_url(url) == "https://example.com/posts/some-article"

    def test_root_path_trailing_slash_is_preserved_as_root(self):
        # Stripping the trailing slash on a bare domain shouldn't eat the path entirely.
        url = "https://example.com/"
        assert canonical_url(url) == "https://example.com"


class TestArticleFingerprint:
    def test_fingerprint_is_a_sha256_hexdigest(self):
        article = _article()
        assert len(article.fingerprint) == 64
        assert all(c in "0123456789abcdef" for c in article.fingerprint)

    def test_fingerprint_stable_across_tracking_param_variants(self):
        base = _article(url="https://example.com/posts/some-article")
        with_utm = _article(
            url="https://example.com/posts/some-article?utm_source=newsletter"
        )
        with_slash = _article(url="https://example.com/posts/some-article/")
        with_fragment = _article(url="https://example.com/posts/some-article#discussion")

        assert base.fingerprint == with_utm.fingerprint
        assert base.fingerprint == with_slash.fingerprint
        assert base.fingerprint == with_fragment.fingerprint

    def test_fingerprint_differs_for_different_articles(self):
        first = _article(url="https://example.com/posts/first-article")
        second = _article(url="https://example.com/posts/second-article")
        assert first.fingerprint != second.fingerprint


class TestArticleTextForSummary:
    def test_uses_body_when_present(self):
        article = _article(body="Full article body.", snippet="Just a snippet.")
        assert article.text_for_summary == "Full article body."

    def test_falls_back_to_snippet_when_body_missing(self):
        article = _article(body="", snippet="Just a snippet.")
        assert article.text_for_summary == "Just a snippet."

    def test_falls_back_to_snippet_when_body_is_whitespace_only(self):
        article = _article(body="   \n  ", snippet="Just a snippet.")
        assert article.text_for_summary == "Just a snippet."


class TestArticleTickerNames:
    def test_defaults_to_an_empty_dict(self):
        article = _article()
        assert article.ticker_names == {}

    def test_maps_a_tags_entry_to_its_display_name(self):
        article = _article(tags=("IONQ",), ticker_names={"IONQ": "IonQ"})
        assert article.ticker_names["IONQ"] == "IonQ"

    def test_can_hold_a_name_for_more_than_one_tag(self):
        article = _article(
            tags=("IONQ", "RGTI"),
            ticker_names={"IONQ": "IonQ", "RGTI": "Rigetti Computing"},
        )
        assert article.ticker_names == {"IONQ": "IonQ", "RGTI": "Rigetti Computing"}
