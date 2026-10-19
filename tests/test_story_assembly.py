"""Tests for assembling a filtered Article into a StorySummary, with no LLM.

Pure fixtures only: synthetic Article instances, no network, no mocking.
"""

from datetime import UTC, datetime

from topic_digest.models import Article
from topic_digest.story_assembly import DEGRADED_THRESHOLD_CHARS, assemble_story


def _article(**overrides) -> Article:
    defaults = dict(
        title="Example Article",
        url="https://example.com/posts/some-article",
        published_at=datetime(2026, 8, 24, tzinfo=UTC),
        publisher="Example Publisher",
        snippet="A short snippet.",
        body="",
    )
    defaults.update(overrides)
    return Article(**defaults)


class TestAssembleStoryIdentity:
    def test_article_and_topic_are_carried_through(self):
        article = _article()
        story = assemble_story(article, topic="quantum")
        assert story.article is article
        assert story.topic == "quantum"

    def test_decoder_terms_is_always_empty(self):
        story = assemble_story(_article(), topic="quantum")
        assert story.decoder_terms == []


class TestSummaryCleanup:
    def test_leading_and_trailing_whitespace_is_stripped(self):
        article = _article(snippet="   A snippet with padding.   ")
        story = assemble_story(article, topic="quantum")
        assert story.summary == "A snippet with padding."

    def test_internal_whitespace_runs_are_collapsed_to_a_single_space(self):
        article = _article(snippet="A   snippet\nwith\t\tirregular   spacing.")
        story = assemble_story(article, topic="quantum")
        assert story.summary == "A snippet with irregular spacing."

    def test_html_entities_are_unescaped(self):
        article = _article(snippet="Ion Q &amp; Rigetti announce a partnership.")
        story = assemble_story(article, topic="quantum")
        assert story.summary == "Ion Q & Rigetti announce a partnership."

    def test_zero_width_spaces_are_stripped_not_turned_into_visible_spaces(self):
        article = _article(snippet="A​snippet​with​zero​width​spaces.")
        story = assemble_story(article, topic="quantum")
        assert "​" not in story.summary
        assert story.summary == "Asnippetwithzerowidthspaces."

    def test_uses_body_over_snippet_when_body_is_present(self):
        article = _article(body="The full body text.", snippet="Just a snippet.")
        story = assemble_story(article, topic="quantum")
        assert story.summary == "The full body text."


class TestDegradedFlag:
    def test_summary_shorter_than_threshold_is_degraded(self):
        article = _article(snippet="x" * (DEGRADED_THRESHOLD_CHARS - 1))
        story = assemble_story(article, topic="quantum")
        assert story.degraded is True

    def test_summary_meeting_threshold_is_not_degraded(self):
        article = _article(snippet="x" * DEGRADED_THRESHOLD_CHARS)
        story = assemble_story(article, topic="quantum")
        assert story.degraded is False

    def test_degraded_check_runs_against_the_cleaned_summary_not_the_raw_snippet(self):
        """Padding whitespace alone shouldn't count toward the threshold."""
        padded_but_thin = "   hi   " + " " * 200
        article = _article(snippet=padded_but_thin)
        story = assemble_story(article, topic="quantum")
        assert story.degraded is True
