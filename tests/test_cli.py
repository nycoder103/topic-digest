"""Tests for the CLI's `run` orchestration.

Every external boundary is mocked: the source adapter's fetch(), the
Gemini call (via google-genai), and the Slack POST. Everything in between
(filtering, story assembly, concept-track pairing, digest assembly) is the
real pipeline code, exercised end to end against a topic config written to
a temp file.
"""

from datetime import UTC, date, datetime

import pytest
import yaml

from topic_digest import cli
from topic_digest.config import (
    ConceptTrackSection,
    DigestSection,
    LLMConfig,
    RelevanceSection,
    SlackConfig,
    SourceSection,
    TopicConfig,
    TopicSection,
)
from topic_digest.models import Article, StorySummary
from topic_digest.sources import register

FAKE_WEBHOOK = "https://hooks.slack.invalid/services/T000/B000/FAKE"

FAKE_ARTICLE = Article(
    title="Quantum Breakthrough Announced",
    url="https://example.invalid/articles/quantum-breakthrough",
    published_at=datetime(2026, 10, 18, tzinfo=UTC),
    publisher="Example Wire",
    snippet="x" * 100,
    body="",
)


class _FakeSource:
    def __init__(self, options: dict, config_dir) -> None:
        self.options = options
        self.config_dir = config_dir

    def fetch(self, since):
        return [FAKE_ARTICLE]


@pytest.fixture(scope="module", autouse=True)
def _register_fake_source():
    register("cli_test_fake_source")(_FakeSource)


class _FakeGenAIModels:
    def __init__(self, calls: list) -> None:
        self._calls = calls

    def generate_content(self, model, contents):
        self._calls.append(("generate_content", model, contents))

        class _Response:
            text = "Generated lesson body."

        return _Response()


class _FakeGenAIClient:
    def __init__(self, calls: list, **kwargs) -> None:
        self.models = _FakeGenAIModels(calls)


def _write_topic_config(tmp_path, **section_overrides) -> str:
    data = {
        "topic": {"id": "quantum", "name": "Quantum Computing", "audience": "engineers"},
        "source": {"adapter": "cli_test_fake_source", "options": {}},
        "relevance": {"keywords": [], "exclude_keywords": [], "min_body_chars": 10},
        "digest": {
            "lookback_days": 7,
            "max_stories": 3,
            "summary_sentences": 3,
            "decoder_terms": 1,
            "taxonomy": ["x"],
        },
        "concept_track": {
            "enabled": True,
            "start_date": "2026-10-18",
            "syllabus": [{"slug": "a", "title": "Lesson A", "prompt": "Teach lesson A."}],
        },
        "llm": {
            "provider": "gemini",
            "model": "gemini-2.5-flash",
            "api_key_env": "GEMINI_API_KEY",
            "max_retries": 2,
        },
        "slack": {
            "webhook_env": "SLACK_WEBHOOK_TEST_ONLY",
            "username": "Test Bot",
            "icon_emoji": ":robot_face:",
        },
    }
    for section, overrides in section_overrides.items():
        data[section].update(overrides)
    path = tmp_path / "topic.yaml"
    path.write_text(yaml.safe_dump(data))
    return str(path)


class TestCliRunOrchestration:
    def test_run_calls_fetch_then_gemini_then_slack_post_in_order(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
        monkeypatch.setenv("SLACK_WEBHOOK_TEST_ONLY", FAKE_WEBHOOK)

        calls: list = []

        def tracking_fetch(self, since):
            calls.append(("fetch", since))
            return [FAKE_ARTICLE]

        monkeypatch.setattr(_FakeSource, "fetch", tracking_fetch)
        monkeypatch.setattr(
            "topic_digest.concept_track.genai.Client",
            lambda **kwargs: _FakeGenAIClient(calls, **kwargs),
        )

        def fake_post(url, json=None, timeout=None):
            calls.append(("slack_post", url, json))

            class _Response:
                status_code = 200

                def raise_for_status(self) -> None:
                    pass

            return _Response()

        monkeypatch.setattr("topic_digest.slack_poster.requests.post", fake_post)

        config_path = _write_topic_config(tmp_path)
        cli.run(config_path)

        kinds = [call[0] for call in calls]
        assert kinds == ["fetch", "generate_content", "slack_post"]

    def test_run_posts_the_assembled_digest_payload(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
        monkeypatch.setenv("SLACK_WEBHOOK_TEST_ONLY", FAKE_WEBHOOK)

        monkeypatch.setattr(
            "topic_digest.concept_track.genai.Client",
            lambda **kwargs: _FakeGenAIClient([], **kwargs),
        )
        posted: dict = {}

        def fake_post(url, json=None, timeout=None):
            posted["url"] = url
            posted["json"] = json

            class _Response:
                status_code = 200

                def raise_for_status(self) -> None:
                    pass

            return _Response()

        monkeypatch.setattr("topic_digest.slack_poster.requests.post", fake_post)

        config_path = _write_topic_config(tmp_path)
        cli.run(config_path)

        assert posted["url"] == FAKE_WEBHOOK
        blocks = posted["json"]["blocks"]
        assert any("Quantum Breakthrough Announced" in str(b) for b in blocks)
        assert any("Generated lesson body." in str(b) for b in blocks)


class TestCliConfigErrorHandling:
    def test_missing_required_section_exits_cleanly_without_a_traceback(self, tmp_path, capsys):
        bad_path = tmp_path / "bad.yaml"
        bad_path.write_text(yaml.safe_dump({"topic": {"id": "x", "name": "X", "audience": "a"}}))

        with pytest.raises(SystemExit) as exc_info:
            cli.main(["run", str(bad_path)])

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Traceback" not in captured.err
        assert "source" in captured.err


def _topic_config(max_stories: int) -> TopicConfig:
    return TopicConfig(
        topic=TopicSection(id="t", name="Topic", audience="engineers"),
        source=SourceSection(adapter="x", options={}),
        relevance=RelevanceSection(keywords=[], min_body_chars=0),
        digest=DigestSection(
            lookback_days=7, max_stories=max_stories, summary_sentences=1, decoder_terms=1,
            taxonomy=["x"],
        ),
        concept_track=ConceptTrackSection(enabled=False, syllabus=[], start_date=date(2025, 1, 1)),
        llm=LLMConfig(provider="gemini", model="m", api_key_env="K", max_retries=1),
        slack=SlackConfig(webhook_env="W", username="u", icon_emoji=":e:"),
    )


def _story(published_at: datetime, title: str) -> StorySummary:
    article = Article(
        title=title,
        url=f"https://example.invalid/{title}",
        published_at=published_at,
        publisher="Example Wire",
        snippet="x" * 50,
        body="",
    )
    return StorySummary(
        article=article, topic="t", summary="summary", decoder_terms=[], degraded=False
    )


class TestAssembleDigest:
    def test_stories_are_ordered_most_recent_first(self):
        older = _story(datetime(2026, 1, 1, tzinfo=UTC), "older")
        newer = _story(datetime(2026, 2, 1, tzinfo=UTC), "newer")
        topic_config = _topic_config(max_stories=5)

        digest = cli.assemble_digest(topic_config, [older, newer], None, datetime.now(UTC))

        assert [s.article.title for s in digest.stories] == ["newer", "older"]

    def test_truncates_to_max_stories(self):
        stories = [
            _story(datetime(2026, 1, day, tzinfo=UTC), f"story-{day}") for day in range(1, 6)
        ]
        topic_config = _topic_config(max_stories=2)

        digest = cli.assemble_digest(topic_config, stories, None, datetime.now(UTC))

        assert len(digest.stories) == 2
        assert [s.article.title for s in digest.stories] == ["story-5", "story-4"]

    def test_topic_id_and_name_and_concept_post_are_carried_through(self):
        from topic_digest.models import ConceptPost

        topic_config = _topic_config(max_stories=5)
        concept_post = ConceptPost(slug="a", title="A", body="b")

        generated_at = datetime(2026, 10, 19, tzinfo=UTC)
        digest = cli.assemble_digest(topic_config, [], concept_post, generated_at)

        assert digest.topic_id == "t"
        assert digest.topic_name == "Topic"
        assert digest.concept_post is concept_post
        assert digest.generated_at == datetime(2026, 10, 19, tzinfo=UTC)
