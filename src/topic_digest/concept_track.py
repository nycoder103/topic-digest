"""Which syllabus lesson a topic teaches this week, and generating it.

Which lesson to send is a pure function of the calendar date (see
pick_weekly_lesson), deliberately with no persisted "which lesson did we
last send" state: `concept_track.start_date` in a topic's config anchors
week 0, and every run recomputes the same answer from today's date and that
anchor. Revisit with real storage later if the syllabus ever needs to
support, say, skipping a week without shifting every lesson after it.

Generation (actually calling the LLM) is the one place in this MVP a model
is used at all: everything else in the pipeline relays source text as-is.
"""

import logging
import os
from datetime import date

from google import genai

from topic_digest.config import ConceptTrackSection, Lesson, LLMConfig
from topic_digest.models import ConceptPost

logger = logging.getLogger(__name__)


class SyllabusExhausted:
    """Sentinel returned by pick_weekly_lesson() when the syllabus has no lesson left."""


SYLLABUS_EXHAUSTED = SyllabusExhausted()


class ConceptPostGenerationError(Exception):
    """Raised when every retry calling the LLM for a concept post has failed."""


def pick_weekly_lesson(
    syllabus: list[Lesson], start_date: date, today: date
) -> Lesson | SyllabusExhausted | None:
    """Pick this week's lesson from a pure function of the calendar date.

    Returns `None` if `start_date` is still in the future (no concept post
    this week), `SYLLABUS_EXHAUSTED` once week_number has run past the end
    of `syllabus` (post the completion message instead), or the matching
    `Lesson` otherwise. Growing `syllabus` later naturally un-exhausts a
    week number that previously ran past its (shorter) old length, since
    this is recomputed fresh every call rather than cached.
    """
    week_number = (today - start_date).days // 7
    if week_number < 0:
        return None
    if week_number >= len(syllabus):
        return SYLLABUS_EXHAUSTED
    return syllabus[week_number]


def _completion_post(topic_name: str, lesson_count: int) -> ConceptPost:
    return ConceptPost(
        slug="syllabus-complete",
        title="Syllabus Complete",
        body=(
            f"This week's digest completes the {lesson_count}-lesson {topic_name} "
            "syllabus. Add more lessons to concept_track.syllabus in config to keep "
            "it going."
        ),
    )


def _generate_lesson_post(lesson: Lesson, llm: LLMConfig) -> ConceptPost:
    client = genai.Client(api_key=os.environ[llm.api_key_env])

    last_error: Exception | None = None
    for attempt in range(1, llm.max_retries + 1):
        try:
            response = client.models.generate_content(model=llm.model, contents=lesson.prompt)
            return ConceptPost(slug=lesson.slug, title=lesson.title, body=response.text)
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Gemini call failed for lesson %r (attempt %d/%d): %s",
                lesson.slug,
                attempt,
                llm.max_retries,
                exc,
            )

    raise ConceptPostGenerationError(
        f"Gemini generation failed for lesson {lesson.slug!r} ({lesson.title!r}) "
        f"after {llm.max_retries} attempt(s)"
    ) from last_error


def build_concept_post(
    concept_track: ConceptTrackSection, llm: LLMConfig, topic_name: str, today: date
) -> ConceptPost | None:
    """Build this week's ConceptPost, or None if there isn't one.

    No LLM call is made unless `concept_track` is enabled and
    pick_weekly_lesson() returns a real Lesson for `today`; an exhausted
    syllabus gets a static completion post instead.
    """
    if not concept_track.enabled:
        return None

    result = pick_weekly_lesson(concept_track.syllabus, concept_track.start_date, today)
    if result is None:
        return None
    if result is SYLLABUS_EXHAUSTED:
        return _completion_post(topic_name, len(concept_track.syllabus))
    return _generate_lesson_post(result, llm)
