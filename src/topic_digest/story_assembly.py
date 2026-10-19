"""Turns one filtered Article into a StorySummary, with no LLM involved.

This MVP relays news stories as the source API reported them (title + its
own short snippet + link) rather than rewriting or expanding them with a
model, so "assembly" here is light text cleanup, not summarization:
real-world feed text comes with HTML entities (`&amp;`) and stray
zero-width spaces that would otherwise leak into a posted Slack message.
decoder_terms is always empty, since there's no LLM pass to produce any.
"""

import re
from html import unescape

from topic_digest.models import Article, StorySummary

# Below this many characters, a cleaned summary is too thin to tell a reader
# anything beyond the headline itself; `degraded` flags that for the poster
# to render differently (see slack_poster.py) rather than presenting it with
# the same confidence as a real summary. Picked as a small, clearly-stub
# threshold: real snippets from a healthy source run well into the hundreds
# of characters (see tests/fixtures/yahoo_finance/README.md's snippet-length
# findings), so anything this short is a stub, not just a terse one.
DEGRADED_THRESHOLD_CHARS = 60

_ZERO_WIDTH_SPACE = "​"
_WHITESPACE_RUN = re.compile(r"\s+")


def _clean(text: str) -> str:
    text = unescape(text)
    text = text.replace(_ZERO_WIDTH_SPACE, "")
    text = _WHITESPACE_RUN.sub(" ", text)
    return text.strip()


def assemble_story(article: Article, topic: str) -> StorySummary:
    """Build the StorySummary for one Article, relayed as-is with no LLM pass."""
    summary = _clean(article.text_for_summary)
    return StorySummary(
        article=article,
        topic=topic,
        summary=summary,
        decoder_terms=[],
        degraded=len(summary) < DEGRADED_THRESHOLD_CHARS,
    )
