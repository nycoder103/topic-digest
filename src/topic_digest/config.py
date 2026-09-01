"""Typed view over a topic's YAML config.

The engine (fetch -> filter -> summarize -> pair with lesson -> post) is the
same code for every topic; everything that differs between topics, like
keywords, syllabus, which LLM, which Slack channel, lives in a YAML file
under config/. These dataclasses are the only place that YAML shape is
described, and load_topic() is the only place it's parsed and validated.
Nothing downstream should read a topic's raw dict; if a topic needs a new
knob, it's added here once, for every topic to use.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ConfigError(Exception):
    """Raised when a topic config file is missing a required key or holds an invalid value."""


def _require(data: dict, key: str, path_prefix: str = ""):
    full_key = f"{path_prefix}.{key}" if path_prefix else key
    try:
        return data[key]
    except KeyError:
        raise ConfigError(f"Missing required config key: {full_key}") from None


@dataclass(frozen=True)
class TopicSection:
    """Identity of the topic: what it's called and who it's for.

    Kept separate from TopicConfig itself so "topic" as a YAML section
    (id/name/audience) doesn't collide with "topic" as the name for the
    whole config file.
    """

    id: str
    name: str
    audience: str

    @classmethod
    def from_dict(cls, data: dict) -> "TopicSection":
        return cls(
            id=_require(data, "id", "topic"),
            name=_require(data, "name", "topic"),
            audience=_require(data, "audience", "topic"),
        )


@dataclass(frozen=True)
class SourceSection:
    """Which fetch adapter to run, and whatever that adapter needs to run it.

    `options` is deliberately untyped here. A ticker-tracking adapter and an
    arXiv adapter need completely different option shapes, and this package
    has no business knowing either one: the adapter named by `adapter`
    validates its own `options` when it's built. See topic_digest.sources.
    (The exception is `load_tickers` below: the "inline list vs. separate
    file, dedupe, enabled flag" shape recurs across every ticker-tracking
    topic, so that parsing lives here once rather than being copy-pasted
    into each such adapter. What a ticker *means* to matching or scoring is
    still entirely the adapter's business.)
    """

    adapter: str
    options: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "SourceSection":
        return cls(
            adapter=_require(data, "adapter", "source"),
            options=data.get("options", {}),
        )


@dataclass(frozen=True)
class TickerEntry:
    """One company in a ticker-tracking topic's universe.

    `name` is not decoration: published articles overwhelmingly refer to
    companies by name ("IonQ"), almost never by bare symbol ("IONQ"), and a
    Slack reader who isn't a market regular needs the name to know what's
    being talked about. Anything that matches articles against this universe
    or renders it downstream needs both forms, so both travel together here
    instead of the name being left to a lookup table somewhere else.
    """

    symbol: str
    name: str
    category: str | None = None
    enabled: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> "TickerEntry":
        return cls(
            symbol=_require(data, "symbol", "tickers[]"),
            name=_require(data, "name", "tickers[]"),
            category=data.get("category"),
            enabled=data.get("enabled", True),
        )


def load_tickers(options: dict, config_dir: str | Path) -> list[TickerEntry]:
    """Resolve a ticker-tracking source's universe from its `options`.

    A ticker list grows and gets re-reviewed on its own schedule, separate
    from prompts, taxonomy, or the posting cadence, so it's allowed to live
    in its own YAML file instead of being inlined into the topic config.
    Exactly one of `tickers` (an inline list) or `tickers_file` (a path) must
    be set; supporting both would leave it ambiguous which list is
    authoritative if they ever disagreed.

    A relative `tickers_file` is resolved against `config_dir`, the topic
    config file's own directory, not the process's current working
    directory, so a topic's ticker file travels with it regardless of where
    the CLI happens to be invoked from.

    Disabled entries are dropped here rather than left for every caller to
    filter; only enabled tickers are ever returned.
    """
    has_inline = "tickers" in options
    has_file = "tickers_file" in options
    if has_inline == has_file:
        raise ConfigError(
            "source.options must set exactly one of 'tickers' or 'tickers_file', got "
            f"{'both' if has_inline else 'neither'}"
        )

    if has_inline:
        raw_entries = options["tickers"]
    else:
        tickers_path = Path(config_dir) / options["tickers_file"]
        try:
            raw = yaml.safe_load(tickers_path.read_text())
        except FileNotFoundError as exc:
            raise ConfigError(f"tickers_file not found: {tickers_path}") from exc
        except yaml.YAMLError as exc:
            raise ConfigError(f"{tickers_path}: invalid YAML ({exc})") from exc

        if not isinstance(raw, dict):
            raise ConfigError(f"{tickers_path}: config file must contain a top-level mapping")
        raw_entries = _require(raw, "tickers")

    entries = [TickerEntry.from_dict(item) for item in raw_entries]

    symbols = [entry.symbol for entry in entries]
    duplicates = sorted({symbol for symbol in symbols if symbols.count(symbol) > 1})
    if duplicates:
        raise ConfigError("Duplicate ticker symbol(s): " + ", ".join(duplicates))

    return [entry for entry in entries if entry.enabled]


@dataclass(frozen=True)
class RelevanceSection:
    """The filter a fetched article has to clear before it's worth summarizing."""

    keywords: list[str]
    min_body_chars: int
    exclude_keywords: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "RelevanceSection":
        return cls(
            keywords=_require(data, "keywords", "relevance"),
            min_body_chars=_require(data, "min_body_chars", "relevance"),
            exclude_keywords=data.get("exclude_keywords", []),
        )


@dataclass(frozen=True)
class DigestSection:
    """Shape and size limits for the weekly output."""

    lookback_days: int
    max_stories: int
    summary_sentences: int
    decoder_terms: int
    taxonomy: list[str]

    @classmethod
    def from_dict(cls, data: dict) -> "DigestSection":
        return cls(
            lookback_days=_require(data, "lookback_days", "digest"),
            max_stories=_require(data, "max_stories", "digest"),
            summary_sentences=_require(data, "summary_sentences", "digest"),
            decoder_terms=_require(data, "decoder_terms", "digest"),
            taxonomy=_require(data, "taxonomy", "digest"),
        )


@dataclass(frozen=True)
class Lesson:
    """One entry in a topic's fixed syllabus: the source material for a ConceptPost."""

    slug: str
    title: str
    prompt: str

    @classmethod
    def from_dict(cls, data: dict) -> "Lesson":
        return cls(
            slug=_require(data, "slug", "concept_track.syllabus[]"),
            title=_require(data, "title", "concept_track.syllabus[]"),
            prompt=_require(data, "prompt", "concept_track.syllabus[]"),
        )


@dataclass(frozen=True)
class ConceptTrackSection:
    """Whether a topic teaches a running syllabus alongside its news, and what's in it.

    Slugs must be unique because they're how the pipeline tracks which
    lessons have already been sent; a duplicate would make two lessons
    indistinguishable to that bookkeeping and risks either repeating or
    silently skipping a lesson.
    """

    enabled: bool
    syllabus: list[Lesson]

    @classmethod
    def from_dict(cls, data: dict) -> "ConceptTrackSection":
        enabled = _require(data, "enabled", "concept_track")
        syllabus = [Lesson.from_dict(item) for item in data.get("syllabus", [])]

        slugs = [lesson.slug for lesson in syllabus]
        duplicates = sorted({slug for slug in slugs if slugs.count(slug) > 1})
        if duplicates:
            raise ConfigError(
                "Duplicate slug(s) in concept_track.syllabus: " + ", ".join(duplicates)
            )

        return cls(enabled=enabled, syllabus=syllabus)


@dataclass(frozen=True)
class LLMConfig:
    """Which model summarizes and writes concept posts for this topic, and how it authenticates."""

    provider: str
    model: str
    api_key_env: str
    max_retries: int

    @classmethod
    def from_dict(cls, data: dict) -> "LLMConfig":
        return cls(
            provider=_require(data, "provider", "llm"),
            model=_require(data, "model", "llm"),
            api_key_env=_require(data, "api_key_env", "llm"),
            max_retries=_require(data, "max_retries", "llm"),
        )


@dataclass(frozen=True)
class SlackConfig:
    """Where and how a topic's digest gets posted.

    `webhook_env` names an environment variable; it is never the webhook
    itself. This repo is public and a Slack webhook URL is a credential, so
    a config that pastes one in directly must fail to load rather than get
    committed.
    """

    webhook_env: str
    username: str
    icon_emoji: str

    @classmethod
    def from_dict(cls, data: dict) -> "SlackConfig":
        webhook_env = _require(data, "webhook_env", "slack")
        if webhook_env.startswith("https://"):
            raise ConfigError(
                "slack.webhook_env must name an environment variable, not start with "
                "'https://'. It looks like a webhook URL was pasted directly into "
                "config; put it in an environment variable and reference that "
                "variable's name here instead."
            )
        return cls(
            webhook_env=webhook_env,
            username=_require(data, "username", "slack"),
            icon_emoji=_require(data, "icon_emoji", "slack"),
        )


@dataclass(frozen=True)
class TopicConfig:
    """The fully parsed, validated contents of one topic's YAML file.

    Every other module should depend on this instead of a raw dict: this is
    the one place a missing or malformed key gets caught, at load time,
    named clearly enough to fix without reading this module's source.
    """

    topic: TopicSection
    source: SourceSection
    relevance: RelevanceSection
    digest: DigestSection
    concept_track: ConceptTrackSection
    llm: LLMConfig
    slack: SlackConfig

    @classmethod
    def from_dict(cls, data: dict) -> "TopicConfig":
        return cls(
            topic=TopicSection.from_dict(_require(data, "topic")),
            source=SourceSection.from_dict(_require(data, "source")),
            relevance=RelevanceSection.from_dict(_require(data, "relevance")),
            digest=DigestSection.from_dict(_require(data, "digest")),
            concept_track=ConceptTrackSection.from_dict(_require(data, "concept_track")),
            llm=LLMConfig.from_dict(_require(data, "llm")),
            slack=SlackConfig.from_dict(_require(data, "slack")),
        )


def load_topic(path: str | Path) -> TopicConfig:
    """Read a topic's YAML file and return its validated config.

    Raises ConfigError, naming the offending key or value, if the file is
    missing a required key or holds something that would break the pipeline
    silently downstream, most importantly a Slack webhook pasted directly
    into config instead of referenced by environment variable name.
    """
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML ({exc})") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: config file must contain a top-level mapping")

    try:
        return TopicConfig.from_dict(raw)
    except ConfigError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
