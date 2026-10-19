"""Tests for concept-track lesson pairing and Gemini generation.

pick_weekly_lesson() is a pure function (no mocking needed, `today` is
injected). build_concept_post() is tested with the google-genai client
entirely mocked: no real call is ever made.
"""

from datetime import date, timedelta

import pytest

from topic_digest.concept_track import (
    SYLLABUS_EXHAUSTED,
    ConceptPostGenerationError,
    build_concept_post,
    pick_weekly_lesson,
)
from topic_digest.config import ConceptTrackSection, Lesson, LLMConfig

LESSON_A = Lesson(slug="a", title="Lesson A", prompt="Teach lesson A.")
LESSON_B = Lesson(slug="b", title="Lesson B", prompt="Teach lesson B.")
LESSON_C = Lesson(slug="c", title="Lesson C", prompt="Teach lesson C.")

START = date(2026, 1, 5)  # a Monday


class TestPickWeeklyLesson:
    def test_start_date_in_the_future_means_no_concept_post(self):
        today = START - timedelta(days=1)
        result = pick_weekly_lesson([LESSON_A, LESSON_B], START, today)
        assert result is None

    def test_week_zero_on_start_date_itself(self):
        result = pick_weekly_lesson([LESSON_A, LESSON_B], START, START)
        assert result == LESSON_A

    def test_week_zero_holds_through_the_first_six_days(self):
        today = date(2026, 1, 11)  # 6 days after START, still week 0
        result = pick_weekly_lesson([LESSON_A, LESSON_B], START, today)
        assert result == LESSON_A

    def test_week_one_begins_on_the_seventh_day(self):
        today = date(2026, 1, 12)  # 7 days after START
        result = pick_weekly_lesson([LESSON_A, LESSON_B], START, today)
        assert result == LESSON_B

    def test_week_number_equal_to_syllabus_length_is_exhausted(self):
        today = date(2026, 1, 19)  # 14 days after START -> week_number 2, len 2
        result = pick_weekly_lesson([LESSON_A, LESSON_B], START, today)
        assert result is SYLLABUS_EXHAUSTED

    def test_week_number_well_past_syllabus_length_is_still_exhausted(self):
        today = date(2027, 1, 1)
        result = pick_weekly_lesson([LESSON_A, LESSON_B], START, today)
        assert result is SYLLABUS_EXHAUSTED

    def test_syllabus_growing_after_exhaustion_resumes_real_lessons(self):
        """A week number that was exhausted against the old (shorter)
        syllabus length must resume indexing into it once more lessons are
        added, not stay permanently treated as exhausted.
        """
        today = date(2026, 1, 19)  # week_number 2
        exhausted_against_old_syllabus = pick_weekly_lesson([LESSON_A, LESSON_B], START, today)
        assert exhausted_against_old_syllabus is SYLLABUS_EXHAUSTED

        resumed = pick_weekly_lesson([LESSON_A, LESSON_B, LESSON_C], START, today)
        assert resumed == LESSON_C


def _concept_track(**overrides) -> ConceptTrackSection:
    defaults = dict(enabled=True, syllabus=[LESSON_A, LESSON_B], start_date=START)
    defaults.update(overrides)
    return ConceptTrackSection(**defaults)


def _llm(**overrides) -> LLMConfig:
    defaults = dict(
        provider="gemini", model="gemini-2.5-flash-lite", api_key_env="GEMINI_API_KEY",
        max_retries=3,
    )
    defaults.update(overrides)
    return LLMConfig(**defaults)


class _FakeModels:
    def __init__(self, responses) -> None:
        self._responses = list(responses)
        self.calls = []

    def generate_content(self, model, contents):
        self.calls.append((model, contents))
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeClient:
    def __init__(self, responses) -> None:
        self.models = _FakeModels(responses)


class TestBuildConceptPostDisabledOrNoPost:
    def test_disabled_concept_track_returns_none_without_calling_llm(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "topic_digest.concept_track.genai.Client",
            lambda **kwargs: calls.append(kwargs) or _FakeClient([]),
        )
        result = build_concept_post(_concept_track(enabled=False), _llm(), "Example Topic", START)
        assert result is None
        assert calls == []

    def test_future_start_date_returns_none_without_calling_llm(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "topic_digest.concept_track.genai.Client",
            lambda **kwargs: calls.append(kwargs) or _FakeClient([]),
        )
        today = date(2025, 1, 1)
        result = build_concept_post(_concept_track(), _llm(), "Example Topic", today)
        assert result is None
        assert calls == []


class TestBuildConceptPostExhausted:
    def test_exhausted_syllabus_builds_a_static_post_without_calling_llm(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "topic_digest.concept_track.genai.Client",
            lambda **kwargs: calls.append(kwargs) or _FakeClient([]),
        )
        today = date(2026, 1, 19)  # week_number 2, syllabus len 2
        post = build_concept_post(_concept_track(), _llm(), "Example Topic", today)

        assert calls == []
        assert post.slug == "syllabus-complete"
        assert "2-lesson Example Topic syllabus" in post.body
        assert "concept_track.syllabus" in post.body


class TestBuildConceptPostGeneration:
    def test_successful_generation_populates_title_body_and_slug_from_the_lesson(
        self, monkeypatch
    ):
        monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
        fake_client = _FakeClient([_FakeResponse("Generated lesson text.")])
        monkeypatch.setattr(
            "topic_digest.concept_track.genai.Client", lambda **kwargs: fake_client
        )

        post = build_concept_post(_concept_track(), _llm(), "Example Topic", START)

        assert post.slug == "a"
        assert post.title == "Lesson A"
        assert post.body == "Generated lesson text."
        assert fake_client.models.calls == [("gemini-2.5-flash-lite", "Teach lesson A.")]

    def test_retries_then_succeeds_within_max_retries(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
        fake_client = _FakeClient(
            [RuntimeError("transient failure"), _FakeResponse("Worked on second try.")]
        )
        monkeypatch.setattr(
            "topic_digest.concept_track.genai.Client", lambda **kwargs: fake_client
        )

        post = build_concept_post(_concept_track(), _llm(max_retries=3), "Example Topic", START)

        assert post.body == "Worked on second try."
        assert len(fake_client.models.calls) == 2

    def test_exhausting_all_retries_raises_a_clear_error(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
        fake_client = _FakeClient([RuntimeError("down")] * 3)
        monkeypatch.setattr(
            "topic_digest.concept_track.genai.Client", lambda **kwargs: fake_client
        )

        with pytest.raises(ConceptPostGenerationError, match="Lesson A|'a'"):
            build_concept_post(_concept_track(), _llm(max_retries=3), "Example Topic", START)

        assert len(fake_client.models.calls) == 3

    def test_api_key_is_read_from_the_configured_env_var(self, monkeypatch):
        monkeypatch.setenv("CUSTOM_GEMINI_KEY", "custom-value")
        captured = {}
        fake_client = _FakeClient([_FakeResponse("ok")])
        monkeypatch.setattr(
            "topic_digest.concept_track.genai.Client",
            lambda **kwargs: captured.update(kwargs) or fake_client,
        )

        build_concept_post(
            _concept_track(), _llm(api_key_env="CUSTOM_GEMINI_KEY"), "Example Topic", START
        )

        assert captured["api_key"] == "custom-value"
