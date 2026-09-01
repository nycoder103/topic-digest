"""Tests for the webhook-redaction backstop in logging_setup.

These exist for one failure mode: an HTTP client raising an exception whose
message embeds the request URL, which then flows through logger.exception()
untouched. The Slack webhook itself is never a value elsewhere in this
codebase (see topic_digest.config.SlackConfig), so this backstop only needs
to catch text that already made it into a message or traceback despite that.

No hooks.slack.com-shaped literal appears anywhere in this file. This repo
and its CI logs are public, and GitHub's push protection (correctly) can't
tell a well-formed fake from a real credential just by looking at the
string. Most tests exercise the redaction machinery against TEST_WEBHOOK, a
same-shape URL on the .invalid TLD (reserved by RFC 2606 for exactly this,
and never a live endpoint) via an injected pattern. A separate test proves
the real production pattern, DEFAULT_WEBHOOK_PATTERN, still matches a
hooks.slack.com URL assembled at runtime from parts, so no scannable literal
sits in the source.
"""

import logging
import re
import sys

import pytest

from topic_digest.logging_setup import (
    DEFAULT_WEBHOOK_PATTERN,
    RedactingFormatter,
    WebhookRedactingFilter,
    configure_logging,
)

TEST_WEBHOOK_PATTERN = re.compile(r"https://hooks\.slack\.invalid/services/\S+")
TEST_WEBHOOK = "https://hooks.slack.invalid/services/T000/B000/FAKE"


def _assemble_real_shaped_webhook() -> str:
    """Build a hooks.slack.com-shaped URL at runtime, not as a literal in source."""
    host = "hooks" + "." + "slack" + "." + "com"
    return f"https://{host}/services/T00000000/B00000000/{'X' * 24}"


@pytest.fixture(autouse=True)
def _reset_root_logger():
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    yield
    root.handlers = original_handlers
    root.setLevel(original_level)


def _make_record(msg, args=()):
    return logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )


class TestDefaultWebhookPattern:
    """Proves the pattern that actually ships in production does its job.

    Everything else in this file tests the redaction *machinery* against
    TEST_WEBHOOK so no real-shaped secret sits in source; this is the one
    test that touches DEFAULT_WEBHOOK_PATTERN itself, and it builds the
    matching URL at runtime so the file still contains no scannable literal.
    """

    def test_matches_a_real_shaped_webhook_url(self):
        assert DEFAULT_WEBHOOK_PATTERN.search(_assemble_real_shaped_webhook()) is not None

    def test_does_not_match_the_invalid_test_host(self):
        assert DEFAULT_WEBHOOK_PATTERN.search(TEST_WEBHOOK) is None


class TestWebhookRedactingFilter:
    def test_redacts_webhook_in_message(self):
        record = _make_record(f"posting to {TEST_WEBHOOK} failed")
        WebhookRedactingFilter(pattern=TEST_WEBHOOK_PATTERN).filter(record)
        assert TEST_WEBHOOK not in record.msg
        assert "[REDACTED" in record.msg

    def test_redacts_webhook_in_tuple_args(self):
        record = _make_record("posting to %s failed", (TEST_WEBHOOK,))
        WebhookRedactingFilter(pattern=TEST_WEBHOOK_PATTERN).filter(record)
        assert TEST_WEBHOOK not in record.args[0]

    def test_redacts_webhook_in_dict_args(self):
        record = _make_record("posting to %(url)s failed", ({"url": TEST_WEBHOOK},))
        WebhookRedactingFilter(pattern=TEST_WEBHOOK_PATTERN).filter(record)
        assert TEST_WEBHOOK not in record.args["url"]

    def test_redacts_webhook_in_exc_text(self):
        record = _make_record("send failed")
        record.exc_text = f"Traceback: ConnectionError: {TEST_WEBHOOK}"
        WebhookRedactingFilter(pattern=TEST_WEBHOOK_PATTERN).filter(record)
        assert TEST_WEBHOOK not in record.exc_text

    def test_leaves_unrelated_text_untouched(self):
        record = _make_record("posting digest for topic 'example' succeeded")
        WebhookRedactingFilter(pattern=TEST_WEBHOOK_PATTERN).filter(record)
        assert record.msg == "posting digest for topic 'example' succeeded"


class TestRedactingFormatter:
    def test_redacts_webhook_baked_into_exc_info_text(self):
        formatter = RedactingFormatter("%(message)s", pattern=TEST_WEBHOOK_PATTERN)
        record = _make_record("send failed")
        try:
            raise ConnectionError(f"POST {TEST_WEBHOOK} failed: connection reset")
        except ConnectionError:
            record.exc_info = sys.exc_info()
            formatted = formatter.format(record)

        assert TEST_WEBHOOK not in formatted
        assert "[REDACTED" in formatted


class TestConfigureLoggingEndToEnd:
    def test_exception_containing_a_webhook_is_redacted_on_stderr(self, capsys):
        configure_logging()
        logger = logging.getLogger("topic_digest.test")
        webhook = _assemble_real_shaped_webhook()

        try:
            raise ConnectionError(f"POST {webhook} failed: connection reset")
        except ConnectionError:
            logger.exception("failed to post digest")

        captured = capsys.readouterr()
        assert webhook not in captured.err
        assert "[REDACTED" in captured.err
        assert "failed to post digest" in captured.err
