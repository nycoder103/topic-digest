"""Tests for the relevance filter.

Pure functions over synthetic Article instances and a RelevanceSection;
no fixtures, no network, no mocking needed.
"""

from datetime import UTC, datetime

from topic_digest.config import RelevanceSection
from topic_digest.filtering import filter_articles
from topic_digest.models import Article


def _article(**overrides) -> Article:
    defaults = dict(
        title="Example Article",
        url=overrides.pop("url", "https://example.com/posts/some-article"),
        published_at=datetime(2026, 8, 24, tzinfo=UTC),
        publisher="Example Publisher",
        snippet="A short snippet.",
        body="",
    )
    defaults.update(overrides)
    return Article(**defaults)


def _relevance(**overrides) -> RelevanceSection:
    defaults = dict(keywords=[], exclude_keywords=[], min_body_chars=0)
    defaults.update(overrides)
    return RelevanceSection(**defaults)


class TestKeywordInclude:
    def test_empty_keywords_list_means_no_include_filter(self):
        article = _article(title="Totally unrelated headline", snippet="Nothing to do with it.")
        relevance = _relevance(keywords=[])
        assert filter_articles([article], relevance) == [article]

    def test_article_without_any_keyword_is_dropped(self):
        article = _article(title="Unrelated headline", snippet="Nothing relevant here.")
        relevance = _relevance(keywords=["quantum"])
        assert filter_articles([article], relevance) == []

    def test_article_matching_keyword_in_title_is_kept(self):
        article = _article(title="A Quantum Computing Breakthrough", snippet="Details.")
        relevance = _relevance(keywords=["quantum"])
        assert filter_articles([article], relevance) == [article]

    def test_article_matching_keyword_in_snippet_is_kept(self):
        article = _article(title="Breakthrough Announced", snippet="A new quantum chip design.")
        relevance = _relevance(keywords=["quantum"])
        assert filter_articles([article], relevance) == [article]

    def test_keyword_match_is_case_insensitive(self):
        article = _article(title="QUANTUM leap forward", snippet="Details.")
        relevance = _relevance(keywords=["quantum"])
        assert filter_articles([article], relevance) == [article]

    def test_any_one_of_multiple_keywords_matching_is_sufficient(self):
        article = _article(title="Big funding round announced", snippet="Details.")
        relevance = _relevance(keywords=["quantum", "funding"])
        assert filter_articles([article], relevance) == [article]


class TestKeywordExclude:
    def test_article_matching_an_exclude_keyword_is_dropped(self):
        article = _article(title="3 Stocks To Watch This Week", snippet="Details.")
        relevance = _relevance(exclude_keywords=["stocks to watch"])
        assert filter_articles([article], relevance) == []

    def test_exclude_match_is_case_insensitive(self):
        article = _article(title="STOCKS TO WATCH This Week", snippet="Details.")
        relevance = _relevance(exclude_keywords=["stocks to watch"])
        assert filter_articles([article], relevance) == []

    def test_exclude_checked_against_snippet_too(self):
        article = _article(title="Headline", snippet="Here's an options alert for traders.")
        relevance = _relevance(exclude_keywords=["options alert"])
        assert filter_articles([article], relevance) == []

    def test_article_not_matching_any_exclude_keyword_is_kept(self):
        article = _article(title="A Real Headline", snippet="Details.")
        relevance = _relevance(exclude_keywords=["price target"])
        assert filter_articles([article], relevance) == [article]

    def test_exclude_wins_even_when_an_include_keyword_also_matches(self):
        article = _article(title="Quantum Stocks To Watch", snippet="Details.")
        relevance = _relevance(keywords=["quantum"], exclude_keywords=["stocks to watch"])
        assert filter_articles([article], relevance) == []


class TestMinBodyChars:
    def test_article_shorter_than_min_body_chars_is_dropped(self):
        article = _article(snippet="short")
        relevance = _relevance(min_body_chars=30)
        assert filter_articles([article], relevance) == []

    def test_article_meeting_min_body_chars_is_kept(self):
        article = _article(snippet="x" * 30)
        relevance = _relevance(min_body_chars=30)
        assert filter_articles([article], relevance) == [article]

    def test_min_body_chars_checked_against_text_for_summary_not_snippet_directly(self):
        """body, when non-empty, wins over snippet in text_for_summary; the
        length check must follow that same fallback, not read snippet
        directly.
        """
        article = _article(body="y" * 30, snippet="short")
        relevance = _relevance(min_body_chars=30)
        assert filter_articles([article], relevance) == [article]

    def test_zero_min_body_chars_keeps_an_empty_snippet_article(self):
        article = _article(snippet="")
        relevance = _relevance(min_body_chars=0)
        assert filter_articles([article], relevance) == [article]


class TestDedupeByFingerprint:
    def test_duplicate_url_keeps_only_first_occurrence(self):
        first = _article(url="https://example.com/a", title="First")
        duplicate = _article(url="https://example.com/a?utm_source=x", title="Second")
        assert filter_articles([first, duplicate], _relevance()) == [first]

    def test_distinct_urls_are_both_kept(self):
        first = _article(url="https://example.com/a")
        second = _article(url="https://example.com/b")
        assert filter_articles([first, second], _relevance()) == [first, second]


class TestCombinedRules:
    def test_order_is_preserved_for_surviving_articles(self):
        a = _article(url="https://example.com/a", title="Quantum story A", snippet="x" * 40)
        b = _article(url="https://example.com/b", title="Quantum story B", snippet="x" * 40)
        c = _article(url="https://example.com/c", title="Unrelated", snippet="x" * 40)
        relevance = _relevance(keywords=["quantum"], min_body_chars=10)

        assert filter_articles([a, b, c], relevance) == [a, b]

    def test_duplicate_that_would_otherwise_fail_a_filter_does_not_resurrect_the_first(self):
        first = _article(url="https://example.com/a", title="Quantum", snippet="x" * 40)
        duplicate_fails_keywords = _article(
            url="https://example.com/a?utm_source=x", title="unrelated", snippet="y" * 40
        )
        relevance = _relevance(keywords=["quantum"], min_body_chars=10)

        assert filter_articles([first, duplicate_fails_keywords], relevance) == [first]
