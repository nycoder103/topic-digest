"""Posts a Digest to Slack as a Block Kit message via an incoming webhook.

The webhook URL is a credential: it's read from `os.environ[...]` inline,
in the same expression that calls requests.post(), and never assigned to a
variable that outlives that call. topic_digest.logging_setup's redaction is
only a backstop for an HTTP library embedding the URL in an exception
message; the primary defense is this function never giving the URL
anywhere else to leak from.
"""

import logging
import os

import requests

from topic_digest.config import SlackConfig
from topic_digest.models import ConceptPost, Digest, StorySummary

logger = logging.getLogger(__name__)

_DEGRADED_MARKER = "_(early/thin report)_"


def _header_block(digest: Digest) -> dict:
    date_str = digest.generated_at.strftime("%B %d, %Y")
    return {
        "type": "header",
        "text": {"type": "plain_text", "text": f"{digest.topic_name}: {date_str}"},
    }


def _story_block(story: StorySummary) -> dict:
    marker = f" {_DEGRADED_MARKER}" if story.degraded else ""
    text = f"*<{story.article.url}|{story.article.title}>*{marker}\n{story.summary}"
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _concept_block(concept_post: ConceptPost) -> dict:
    text = f"*{concept_post.title}*\n{concept_post.body}"
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _build_blocks(digest: Digest) -> list[dict]:
    blocks = [_header_block(digest)]
    blocks.extend(_story_block(story) for story in digest.stories)
    blocks.append({"type": "divider"})
    if digest.concept_post is not None:
        blocks.append(_concept_block(digest.concept_post))
    return blocks


def post_digest(digest: Digest, slack: SlackConfig) -> bool:
    """POST digest to Slack, or skip and return False if there's nothing to post.

    Raises on a non-2xx response from Slack. Returns True once the POST has
    succeeded.
    """
    if digest.is_empty:
        logger.info("Digest for topic '%s' has nothing to post; skipping", digest.topic_id)
        return False

    payload = {
        "username": slack.username,
        "icon_emoji": slack.icon_emoji,
        "blocks": _build_blocks(digest),
    }

    response = requests.post(os.environ[slack.webhook_env], json=payload, timeout=30)
    response.raise_for_status()
    logger.info("Posted digest for topic '%s' to Slack", digest.topic_id)
    return True
