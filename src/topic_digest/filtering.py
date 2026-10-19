"""The junk filter an Article has to clear before it's worth assembling into a story.

This runs after a source adapter's fetch() and before story_assembly: by the
time an Article reaches here, dedup against this topic's ticker universe (or
whatever a source's own matching does) has already happened inside the
adapter. What's left to filter here is topic-relevance (keyword include/
exclude) and basic quality (a near-empty snippet isn't worth summarizing),
plus a second dedup pass since two different source calls, or two tickers in
the same fetch, can both surface the same story under different URLs.

Everything here is a pure function over `list[Article]` and a topic's
RelevanceSection: no network, no I/O, nothing that can't run against
synthetic fixtures in a unit test.
"""

from topic_digest.config import RelevanceSection
from topic_digest.models import Article


def _searchable_text(article: Article) -> str:
    return f"{article.title} {article.snippet}".lower()


def _matches_any_keyword(article: Article, keywords: list[str]) -> bool:
    if not keywords:
        return True
    text = _searchable_text(article)
    return any(keyword.lower() in text for keyword in keywords)


def _matches_any_exclude_keyword(article: Article, exclude_keywords: list[str]) -> bool:
    text = _searchable_text(article)
    return any(keyword.lower() in text for keyword in exclude_keywords)


def _dedupe_by_fingerprint(articles: list[Article]) -> list[Article]:
    seen: set[str] = set()
    deduped = []
    for article in articles:
        fingerprint = article.fingerprint
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        deduped.append(article)
    return deduped


def filter_articles(articles: list[Article], relevance: RelevanceSection) -> list[Article]:
    """Return the articles that pass this topic's relevance and quality bar.

    Rules, in order: dedup by fingerprint (keep first occurrence), then for
    each surviving article: must match at least one of `relevance.keywords`
    (an empty list means no include-filter is applied at all), must not
    match any of `relevance.exclude_keywords`, and must have at least
    `relevance.min_body_chars` characters of `Article.text_for_summary`.
    Matching is a case-insensitive substring check against the article's
    title and snippet combined.
    """
    survivors = []
    for article in _dedupe_by_fingerprint(articles):
        if not _matches_any_keyword(article, relevance.keywords):
            continue
        if _matches_any_exclude_keyword(article, relevance.exclude_keywords):
            continue
        if len(article.text_for_summary) < relevance.min_body_chars:
            continue
        survivors.append(article)
    return survivors
