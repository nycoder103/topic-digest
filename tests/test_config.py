"""Tests for topic config loading and validation.

`load_topic` is the only place a topic's raw YAML gets turned into typed,
trusted data, so these tests lean hardest on the two failure modes that
matter most: a broken or ambiguous syllabus, and a Slack webhook URL pasted
into a public repo instead of referenced by environment variable name.
"""

from pathlib import Path

import pytest
import yaml

from topic_digest.config import ConfigError, TickerEntry, TopicConfig, load_tickers, load_topic

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _valid_config() -> dict:
    return {
        "topic": {
            "id": "example-topic",
            "name": "Example Topic",
            "audience": "engineers",
        },
        "source": {
            "adapter": "rss",
            "options": {"feed_urls": ["https://example.com/feed.xml"]},
        },
        "relevance": {
            "keywords": ["example"],
            "exclude_keywords": ["sponsored"],
            "min_body_chars": 200,
        },
        "digest": {
            "lookback_days": 7,
            "max_stories": 5,
            "summary_sentences": 3,
            "decoder_terms": 2,
            "taxonomy": ["patterns"],
        },
        "concept_track": {
            "enabled": True,
            "syllabus": [
                {"slug": "lesson-one", "title": "Lesson One", "prompt": "Explain lesson one."},
                {"slug": "lesson-two", "title": "Lesson Two", "prompt": "Explain lesson two."},
            ],
        },
        "llm": {
            "provider": "google",
            "model": "gemini-2.5-flash",
            "api_key_env": "GEMINI_API_KEY",
            "max_retries": 3,
        },
        "slack": {
            "webhook_env": "SLACK_WEBHOOK_EXAMPLE",
            "username": "Example Bot",
            "icon_emoji": ":robot_face:",
        },
    }


def _write_config(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "topic.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


class TestLoadTopicValid:
    def test_loads_a_fully_populated_config(self, tmp_path):
        path = _write_config(tmp_path, _valid_config())
        config = load_topic(path)

        assert isinstance(config, TopicConfig)
        assert config.topic.id == "example-topic"
        assert config.source.adapter == "rss"
        assert config.source.options == {"feed_urls": ["https://example.com/feed.xml"]}
        assert config.relevance.min_body_chars == 200
        assert config.digest.taxonomy == ["patterns"]
        assert [lesson.slug for lesson in config.concept_track.syllabus] == [
            "lesson-one",
            "lesson-two",
        ]
        assert config.llm.provider == "google"
        assert config.slack.username == "Example Bot"

    def test_options_defaults_to_empty_dict_when_omitted(self, tmp_path):
        data = _valid_config()
        del data["source"]["options"]
        config = load_topic(_write_config(tmp_path, data))
        assert config.source.options == {}

    def test_exclude_keywords_defaults_to_empty_list_when_omitted(self, tmp_path):
        data = _valid_config()
        del data["relevance"]["exclude_keywords"]
        config = load_topic(_write_config(tmp_path, data))
        assert config.relevance.exclude_keywords == []


class TestLoadTopicMissingKeys:
    @pytest.mark.parametrize(
        "section", ["topic", "source", "relevance", "digest", "concept_track", "llm", "slack"]
    )
    def test_missing_top_level_section_raises_config_error(self, tmp_path, section):
        data = _valid_config()
        del data[section]
        path = _write_config(tmp_path, data)

        with pytest.raises(ConfigError, match=section):
            load_topic(path)

    @pytest.mark.parametrize(
        ("section", "key"),
        [
            ("topic", "id"),
            ("topic", "name"),
            ("topic", "audience"),
            ("source", "adapter"),
            ("relevance", "keywords"),
            ("relevance", "min_body_chars"),
            ("digest", "lookback_days"),
            ("digest", "max_stories"),
            ("digest", "taxonomy"),
            ("concept_track", "enabled"),
            ("llm", "provider"),
            ("llm", "api_key_env"),
            ("slack", "webhook_env"),
            ("slack", "username"),
        ],
    )
    def test_missing_nested_key_names_it_in_the_error(self, tmp_path, section, key):
        data = _valid_config()
        del data[section][key]
        path = _write_config(tmp_path, data)

        with pytest.raises(ConfigError, match=f"{section}.{key}"):
            load_topic(path)

    def test_missing_syllabus_lesson_field_names_it_in_the_error(self, tmp_path):
        data = _valid_config()
        del data["concept_track"]["syllabus"][0]["slug"]
        path = _write_config(tmp_path, data)

        with pytest.raises(ConfigError, match="slug"):
            load_topic(path)


class TestLoadTopicSemanticValidation:
    def test_duplicate_syllabus_slugs_raise_config_error(self, tmp_path):
        data = _valid_config()
        data["concept_track"]["syllabus"][1]["slug"] = data["concept_track"]["syllabus"][0][
            "slug"
        ]
        path = _write_config(tmp_path, data)

        with pytest.raises(ConfigError, match="lesson-one"):
            load_topic(path)

    def test_webhook_env_holding_a_url_is_rejected(self, tmp_path):
        data = _valid_config()
        data["slack"]["webhook_env"] = "https://hooks.slack.com/services/T000/B000/xxxxxxxx"
        path = _write_config(tmp_path, data)

        with pytest.raises(ConfigError, match="https://"):
            load_topic(path)

    def test_webhook_env_as_a_plain_variable_name_is_accepted(self, tmp_path):
        data = _valid_config()
        data["slack"]["webhook_env"] = "SLACK_WEBHOOK_URL"
        config = load_topic(_write_config(tmp_path, data))
        assert config.slack.webhook_env == "SLACK_WEBHOOK_URL"


class TestLoadTopicMalformedFile:
    def test_empty_file_raises_config_error(self, tmp_path):
        path = tmp_path / "empty.yaml"
        path.write_text("")

        with pytest.raises(ConfigError):
            load_topic(path)

    def test_non_mapping_top_level_raises_config_error(self, tmp_path):
        path = tmp_path / "list.yaml"
        path.write_text(yaml.safe_dump(["not", "a", "mapping"]))

        with pytest.raises(ConfigError):
            load_topic(path)


class TestRealConfigFiles:
    """Guards real topic configs under config/, not the schema itself.

    No example or demo file is kept here on purpose: a checked-in sample
    goes stale the moment the schema changes elsewhere and nobody notices,
    which is worse than no example at all. The schema itself is already
    fully exercised by the tests above; this just makes sure that whatever
    real topics land in config/ later keep loading in CI.
    """

    def test_every_config_file_loads_without_error(self):
        for path in sorted(CONFIG_DIR.glob("*.yaml")):
            load_topic(path)


def _write_yaml(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data))
    return path


class TestLoadTickers:
    def test_inline_list_is_loaded(self, tmp_path):
        options = {
            "tickers": [
                {"symbol": "IONQ", "name": "IonQ", "category": "quantum"},
                {"symbol": "RGTI", "name": "Rigetti Computing"},
            ]
        }

        entries = load_tickers(options, tmp_path)

        assert entries == [
            TickerEntry(symbol="IONQ", name="IonQ", category="quantum"),
            TickerEntry(symbol="RGTI", name="Rigetti Computing"),
        ]

    def test_file_reference_is_loaded(self, tmp_path):
        _write_yaml(
            tmp_path / "tickers.yaml",
            {"tickers": [{"symbol": "IONQ", "name": "IonQ"}]},
        )
        options = {"tickers_file": "tickers.yaml"}

        entries = load_tickers(options, tmp_path)

        assert entries == [TickerEntry(symbol="IONQ", name="IonQ")]

    def test_relative_path_resolves_against_config_dir_not_cwd(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        _write_yaml(
            config_dir / "tickers" / "quantum.yaml",
            {"tickers": [{"symbol": "IONQ", "name": "IonQ"}]},
        )
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        entries = load_tickers({"tickers_file": "tickers/quantum.yaml"}, config_dir)

        assert entries == [TickerEntry(symbol="IONQ", name="IonQ")]

    def test_both_tickers_and_tickers_file_raises_config_error_naming_both_keys(self, tmp_path):
        _write_yaml(tmp_path / "tickers.yaml", {"tickers": []})
        options = {
            "tickers": [{"symbol": "IONQ", "name": "IonQ"}],
            "tickers_file": "tickers.yaml",
        }

        with pytest.raises(ConfigError) as exc_info:
            load_tickers(options, tmp_path)

        assert "tickers" in str(exc_info.value)
        assert "tickers_file" in str(exc_info.value)

    def test_neither_tickers_nor_tickers_file_raises_config_error_naming_both_keys(
        self, tmp_path
    ):
        with pytest.raises(ConfigError) as exc_info:
            load_tickers({}, tmp_path)

        assert "tickers" in str(exc_info.value)
        assert "tickers_file" in str(exc_info.value)

    def test_duplicate_symbols_raise_config_error(self, tmp_path):
        options = {
            "tickers": [
                {"symbol": "IONQ", "name": "IonQ"},
                {"symbol": "IONQ", "name": "IonQ Inc."},
            ]
        }

        with pytest.raises(ConfigError, match="IONQ"):
            load_tickers(options, tmp_path)

    def test_disabled_entries_are_filtered_out(self, tmp_path):
        options = {
            "tickers": [
                {"symbol": "IONQ", "name": "IonQ", "enabled": True},
                {"symbol": "RGTI", "name": "Rigetti Computing", "enabled": False},
            ]
        }

        entries = load_tickers(options, tmp_path)

        assert [entry.symbol for entry in entries] == ["IONQ"]

    def test_enabled_defaults_to_true_when_omitted(self, tmp_path):
        entries = load_tickers({"tickers": [{"symbol": "IONQ", "name": "IonQ"}]}, tmp_path)
        assert entries[0].enabled is True
