"""Command-line entry point for topic-digest.

`topic-digest run <config path>` runs the full pipeline for one topic:
fetch -> filter -> assemble stories -> pick/generate this week's concept
post -> assemble into a Digest -> post to Slack. This is a single-topic
MVP; iterating over several topics in one run is out of scope here and can
be added later as a thin loop around `run()` without changing it.
"""

import argparse
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from topic_digest.concept_track import build_concept_post
from topic_digest.config import ConfigError, TopicConfig, load_topic
from topic_digest.filtering import filter_articles
from topic_digest.logging_setup import configure_logging
from topic_digest.models import ConceptPost, Digest, StorySummary
from topic_digest.slack_poster import post_digest

# Imported for their registration side effect only: each module registers
# itself with topic_digest.sources under the adapter name a topic config
# names in source.adapter.
from topic_digest.sources import alpha_vantage, build, yahoo_finance  # noqa: F401
from topic_digest.story_assembly import assemble_story

logger = logging.getLogger(__name__)


def assemble_digest(
    topic_config: TopicConfig,
    stories: list[StorySummary],
    concept_post: ConceptPost | None,
    generated_at: datetime,
) -> Digest:
    """Combine this week's stories and concept post into one Digest.

    Stories are ordered most-recent-first and truncated to
    `topic_config.digest.max_stories`.
    """
    ordered = sorted(stories, key=lambda story: story.article.published_at, reverse=True)
    return Digest(
        topic_id=topic_config.topic.id,
        topic_name=topic_config.topic.name,
        generated_at=generated_at,
        stories=ordered[: topic_config.digest.max_stories],
        concept_post=concept_post,
    )


def run(config_path: str) -> None:
    """Run the full pipeline for one topic's config file."""
    configure_logging()
    topic_config = load_topic(config_path)

    source = build(
        topic_config.source.adapter, topic_config.source.options, Path(config_path).parent
    )
    now = datetime.now(UTC)
    since = now - timedelta(days=topic_config.digest.lookback_days)

    articles = source.fetch(since)
    logger.info("Fetched %s article(s) for topic '%s'", len(articles), topic_config.topic.id)

    filtered = filter_articles(articles, topic_config.relevance)
    logger.info("%s article(s) passed the relevance filter", len(filtered))

    stories = [assemble_story(article, topic_config.topic.id) for article in filtered]

    concept_post = build_concept_post(
        topic_config.concept_track, topic_config.llm, topic_config.topic.name, now.date()
    )

    digest = assemble_digest(topic_config, stories, concept_post, now)
    post_digest(digest, topic_config.slack)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="topic-digest")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run", help="Fetch, filter, assemble, and post one topic's weekly digest."
    )
    run_parser.add_argument("config_path", help="Path to the topic's YAML config file.")

    args = parser.parse_args(argv)

    if args.command == "run":
        try:
            run(args.config_path)
        except ConfigError as exc:
            print(f"topic-digest: {exc}", file=sys.stderr)
            raise SystemExit(1) from None


if __name__ == "__main__":
    main()
