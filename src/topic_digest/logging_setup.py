"""Backstop redaction for anything that logs a Slack webhook URL.

The webhook URL should never exist as a value anywhere in this codebase; it
lives in an environment variable, read at send time, and nothing else holds
a reference to it (see topic_digest.config.SlackConfig, which rejects a
webhook URL pasted into YAML for the same reason). This module exists for
the failure mode that can't be designed away: an HTTP library raising an
exception whose message happens to embed the request URL, which then flows
into a log line or traceback untouched. It is a backstop, not the primary
defense; the primary defense is that the credential never appears in code or
config to begin with.

DEFAULT_WEBHOOK_PATTERN matches the real Slack webhook shape and is what
runs in production. Both classes below accept a `pattern` override so tests
can exercise the redaction machinery itself against a pattern that can't be
mistaken for a real credential (this repo is public, and so is CI's log
output) without ever weakening what actually ships.
"""

import logging
import re

DEFAULT_WEBHOOK_PATTERN = re.compile(r"https://hooks\.slack\.com/services/\S+")
_REDACTED = "[REDACTED SLACK WEBHOOK]"


class WebhookRedactingFilter(logging.Filter):
    """Scrubs webhook-shaped URLs out of a LogRecord before a handler emits it.

    Covers the message and both %-style argument forms (tuple and dict),
    since either can carry a URL that only becomes part of the text at
    format time. Also scrubs exc_text in case an earlier handler already
    rendered a traceback onto this record; the harder case, a traceback
    rendered for the first time by *this* handler, is covered by
    RedactingFormatter below, since exc_text doesn't exist until format()
    creates it.
    """

    def __init__(self, pattern: re.Pattern[str] = DEFAULT_WEBHOOK_PATTERN) -> None:
        super().__init__()
        self._pattern = pattern

    def _redact(self, text: str) -> str:
        return self._pattern.sub(_REDACTED, text)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._redact(str(record.msg))

        if isinstance(record.args, dict):
            record.args = {k: self._redact(str(v)) for k, v in record.args.items()}
        elif isinstance(record.args, tuple):
            record.args = tuple(self._redact(str(a)) for a in record.args)

        if record.exc_text:
            record.exc_text = self._redact(record.exc_text)

        return True


class RedactingFormatter(logging.Formatter):
    """Redacts after formatting, since that's the only point exc_info becomes text.

    logging.Formatter.format() is what calls formatException() and appends
    the resulting traceback text to the output; nothing upstream of it ever
    sees that text as a string. A Filter can't catch a secret embedded in a
    traceback for that reason, so this formatter re-scrubs its own output as
    a last line of defense.
    """

    def __init__(
        self, *args: object, pattern: re.Pattern[str] = DEFAULT_WEBHOOK_PATTERN, **kwargs: object
    ) -> None:
        super().__init__(*args, **kwargs)
        self._pattern = pattern

    def format(self, record: logging.LogRecord) -> str:
        return self._pattern.sub(_REDACTED, super().format(record))


def configure_logging(level: int = logging.INFO) -> None:
    """Install a single redacting stderr handler on the root logger.

    Safe to call more than once: existing handlers are cleared first so
    repeated calls (e.g. across tests, or a re-exec) don't stack duplicate
    output.
    """
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    handler = logging.StreamHandler()
    handler.addFilter(WebhookRedactingFilter())
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
