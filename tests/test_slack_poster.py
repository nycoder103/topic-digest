"""Tests for the Slack poster.

requests.post is mocked entirely; no test ever makes a network call or
holds a real-shaped webhook URL. SLACK_WEBHOOK_TEST_ONLY, pointing at a
hooks.slack.invalid-style fake value, follows the same convention as
tests/test_logging_setup.py's TEST_WEBHOOK.
"""

from datetime import UTC, datetime

import pytest

from topic_digest.config import SlackConfig
from topic_digest.models import Article, ConceptPost, Digest, StorySummary
from topic_digest.slack_poster import post_digest

FAKE_WEBHOOK = "https://hooks.slack.invalid/services/T000/B000/FAKE"


@pytest.fixture(autouse=True)
def _webhook_env(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_TEST_ONLY", FAKE_WEBHOOK)


def _slack_config(**overrides) -> SlackConfig:
    defaults = dict(
        webhook_env="SLACK_WEBHOOK_TEST_ONLY", username="Test Bot", icon_emoji=":robot_face:"
    )
    defaults.update(overrides)
    return SlackConfig(**defaults)


def _article(**overrides) -> Article:
    defaults = dict(
        title="A Quantum Headline",
        url="https://example.invalid/articles/quantum-headline",
        published_at=datetime(2026, 10, 18, tzinfo=UTC),
        publisher="Example Wire",
        snippet="A snippet.",
        body="",
    )
    defaults.update(overrides)
    return Article(**defaults)


def _story(**overrides) -> StorySummary:
    defaults = dict(
        article=_article(), topic="quantum", summary="A clean summary.", decoder_terms=[],
        degraded=False,
    )
    defaults.update(overrides)
    return StorySummary(**defaults)


def _digest(**overrides) -> Digest:
    defaults = dict(
        topic_id="quantum",
        topic_name="Quantum Computing",
        generated_at=datetime(2026, 10, 19, tzinfo=UTC),
        stories=[_story()],
        concept_post=None,
    )
    defaults.update(overrides)
    return Digest(**defaults)


class _FakeResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"simulated HTTP {self.status_code}")


def _patch_post(monkeypatch, status_code: int = 200, capture: dict | None = None):
    def fake_post(url, json=None, timeout=None):
        if capture is not None:
            capture["url"] = url
            capture["json"] = json
            capture["timeout"] = timeout
        return _FakeResponse(status_code)

    monkeypatch.setattr("topic_digest.slack_poster.requests.post", fake_post)


class TestPostDigestSkipsWhenEmpty:
    def test_empty_digest_is_not_posted(self, monkeypatch, caplog):
        capture: dict = {}
        _patch_post(monkeypatch, capture=capture)
        digest = _digest(stories=[], concept_post=None)

        with caplog.at_level("INFO"):
            result = post_digest(digest, _slack_config())

        assert capture == {}
        assert result is False
        assert any("nothing to post" in r.getMessage().lower() for r in caplog.records)


class TestPostDigestRequestShape:
    def test_webhook_url_is_read_from_the_configured_env_var(self, monkeypatch):
        capture: dict = {}
        _patch_post(monkeypatch, capture=capture)
        post_digest(_digest(), _slack_config())
        assert capture["url"] == FAKE_WEBHOOK

    def test_payload_includes_username_and_icon_emoji(self, monkeypatch):
        capture: dict = {}
        _patch_post(monkeypatch, capture=capture)
        post_digest(_digest(), _slack_config(username="Quantum Digest", icon_emoji=":atom:"))

        payload = capture["json"]
        assert payload["username"] == "Quantum Digest"
        assert payload["icon_emoji"] == ":atom:"

    def test_payload_has_a_header_block_with_topic_name(self, monkeypatch):
        capture: dict = {}
        _patch_post(monkeypatch, capture=capture)
        post_digest(_digest(topic_name="Quantum Computing"), _slack_config())

        blocks = capture["json"]["blocks"]
        header = blocks[0]
        assert header["type"] == "header"
        assert "Quantum Computing" in header["text"]["text"]

    def test_payload_has_one_section_block_per_story_linking_the_url(self, monkeypatch):
        capture: dict = {}
        _patch_post(monkeypatch, capture=capture)
        story_a = _story(article=_article(url="https://example.invalid/a", title="Story A"))
        story_b = _story(article=_article(url="https://example.invalid/b", title="Story B"))
        post_digest(_digest(stories=[story_a, story_b]), _slack_config())

        blocks = capture["json"]["blocks"]
        story_blocks = [b for b in blocks if b["type"] == "section"]
        texts = [b["text"]["text"] for b in story_blocks]
        assert any("https://example.invalid/a" in t and "Story A" in t for t in texts)
        assert any("https://example.invalid/b" in t and "Story B" in t for t in texts)

    def test_story_summary_text_appears_in_its_block(self, monkeypatch):
        capture: dict = {}
        _patch_post(monkeypatch, capture=capture)
        story = _story(summary="This is the story summary text.")
        post_digest(_digest(stories=[story]), _slack_config())

        blocks = capture["json"]["blocks"]
        assert any(
            "This is the story summary text." in b.get("text", {}).get("text", "")
            for b in blocks
        )

    def test_degraded_story_gets_a_visual_marker_non_degraded_does_not(self, monkeypatch):
        capture: dict = {}
        _patch_post(monkeypatch, capture=capture)
        degraded = _story(
            article=_article(url="https://example.invalid/degraded"), degraded=True
        )
        healthy = _story(article=_article(url="https://example.invalid/healthy"), degraded=False)
        post_digest(_digest(stories=[degraded, healthy]), _slack_config())

        blocks = capture["json"]["blocks"]
        degraded_text = next(
            b["text"]["text"] for b in blocks if "example.invalid/degraded" in b["text"]["text"]
        )
        healthy_text = next(
            b["text"]["text"] for b in blocks if "example.invalid/healthy" in b["text"]["text"]
        )
        assert degraded_text != healthy_text.replace("healthy", "degraded")
        assert "thin" in degraded_text.lower() or "early" in degraded_text.lower()

    def test_payload_includes_a_divider(self, monkeypatch):
        capture: dict = {}
        _patch_post(monkeypatch, capture=capture)
        post_digest(_digest(), _slack_config())

        blocks = capture["json"]["blocks"]
        assert any(b["type"] == "divider" for b in blocks)

    def test_concept_post_section_includes_title_and_body_when_present(self, monkeypatch):
        capture: dict = {}
        _patch_post(monkeypatch, capture=capture)
        concept_post = ConceptPost(slug="a", title="What Is a Qubit?", body="A qubit is...")
        post_digest(_digest(concept_post=concept_post), _slack_config())

        blocks = capture["json"]["blocks"]
        assert any(
            "What Is a Qubit?" in b.get("text", {}).get("text", "")
            and "A qubit is..." in b.get("text", {}).get("text", "")
            for b in blocks
        )

    def test_no_concept_section_when_concept_post_is_none(self, monkeypatch):
        capture: dict = {}
        _patch_post(monkeypatch, capture=capture)
        post_digest(_digest(concept_post=None), _slack_config())

        blocks = capture["json"]["blocks"]
        assert len(blocks) == 1 + len(_digest().stories) + 1  # header + stories + divider


class TestPostDigestErrorHandling:
    def test_non_2xx_response_raises(self, monkeypatch):
        _patch_post(monkeypatch, status_code=500)
        with pytest.raises(RuntimeError):
            post_digest(_digest(), _slack_config())

    def test_returns_true_on_success(self, monkeypatch):
        _patch_post(monkeypatch, status_code=200)
        assert post_digest(_digest(), _slack_config()) is True


class TestPostDigestNeverLogsTheWebhook:
    def test_webhook_url_never_appears_in_log_output(self, monkeypatch, caplog):
        _patch_post(monkeypatch)
        with caplog.at_level("DEBUG"):
            post_digest(_digest(), _slack_config())

        for record in caplog.records:
            assert FAKE_WEBHOOK not in record.getMessage()
