"""Shared data model for the digest pipeline.

One topic's config drives fetching, filtering, summarizing, and posting, but
all four stages are the same code no matter which topic is running. That only
works if every stage agrees on the shape of an article, a summary, and a
digest before any topic-specific logic touches them. These are frozen
dataclasses (not dicts, not topic-specific subclasses) so a stage can't
silently mutate what an earlier stage produced, and so adding a second topic
never means touching this file.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit


def canonical_url(url: str) -> str:
    """Reduce a URL to the form used for deduplication and fingerprinting.

    The same story gets shared with different tracking params (utm_source,
    referral codes, in-page anchors) depending on who links it, and some
    sources add or drop a trailing slash inconsistently. Without a single
    canonical form, the same article would be treated as new every time it
    reappears under a slightly different URL, and would show up more than
    once in a digest.
    """
    parsed = urlsplit(url)
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


@dataclass(frozen=True)
class Article:
    """A single piece of source content, before any topic-specific judgment is applied.

    This is the boundary between "fetching" and everything downstream:
    filtering, summarizing, and pairing with a lesson all operate on an
    Article and never need to know which fetcher produced it or which topic
    it belongs to. Keeping it frozen means a filter or summarizer can't
    quietly rewrite the source record that later stages (or a re-run) would
    rely on.
    """

    title: str
    url: str
    published_at: datetime
    publisher: str
    snippet: str
    body: str
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def fingerprint(self) -> str:
        """A stable identity for this article, independent of URL noise.

        Used to dedupe across fetches and across weeks, so the same story
        reappearing under a different tracking URL doesn't get posted twice.
        """
        return hashlib.sha256(canonical_url(self.url).encode("utf-8")).hexdigest()

    @property
    def text_for_summary(self) -> str:
        """The best available text to summarize.

        Full article bodies frequently can't be fetched (paywalls, JS-rendered
        pages, robots.txt), but a snippet from the source feed almost always
        is. Centralizing the fallback here means the summarizer never has to
        special-case a missing body, and a StorySummary can be marked
        degraded based on what this actually returned.
        """
        return self.body if self.body.strip() else self.snippet


@dataclass(frozen=True)
class DecoderTerm:
    """One piece of jargon a reader would need explained to follow a story.

    Kept separate from the summary text so a rendering layer (Slack blocks,
    email, plain text) can decide how to format a glossary without parsing it
    back out of prose.
    """

    term: str
    definition: str


@dataclass(frozen=True)
class StorySummary:
    """The result of turning one Article into digest-ready content for one topic.

    The same article could in principle be summarized differently for two
    topics that both track it, which is why the topic label travels with the
    summary rather than living on the Article itself. The `degraded` flag
    exists because a summarizer working from a thin snippet can produce
    something technically well-formed but not actually informative; carrying
    that signal forward lets a later stage decide to drop or flag the story
    instead of posting a confidently-worded summary of almost nothing.
    """

    article: Article
    topic: str
    summary: str
    decoder_terms: list[DecoderTerm]
    degraded: bool


@dataclass(frozen=True)
class ConceptPost:
    """A lesson pulled from a topic's fixed syllabus, paired alongside that week's stories.

    This is what makes a digest teach a subject instead of just reporting on
    it. It stands apart from StorySummary because it isn't derived from a
    fetched article at all; it comes from topic config, and needs its own
    identity (`slug`) so a topic's syllabus can track which lessons have
    already been sent.
    """

    slug: str
    title: str
    body: str


@dataclass(frozen=True)
class Digest:
    """One topic's complete output for one week: the unit that actually gets posted.

    Everything upstream (fetch, filter, summarize, pair with a lesson)
    produces pieces; this is what those pieces are assembled into before
    handing off to the poster. Scoping it to a single topic and a single
    generation timestamp keeps a multi-topic scheduler simple: it builds one
    Digest per topic per run and posts each independently.
    """

    topic_id: str
    topic_name: str
    generated_at: datetime
    stories: list[StorySummary]
    concept_post: ConceptPost | None = None

    @property
    def is_empty(self) -> bool:
        """Whether there's nothing worth posting this week.

        A run can come up empty if nothing new passed the filter and the
        topic's syllabus has no lesson queued. Giving the poster a single
        check here means it doesn't need to know the rules for what counts as
        "nothing to say."

        A week with a concept post but no stories is deliberately not
        empty: news volume is bursty and some weeks won't produce anything
        worth summarizing, but the syllabus still has a lesson queued. That's
        the fixed syllabus doing its job of keeping the digest alive through
        quiet weeks, not a degenerate case to filter out.
        """
        return not self.stories and self.concept_post is None
