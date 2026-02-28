"""Tests for structured JSON logging and configuration."""

import json
import logging

import pytest

from cordfeeder.config import Config
from cordfeeder.main import JSONFormatter


class TestJSONFormatter:
    def test_basic_log_entry(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        output = json.loads(formatter.format(record))
        assert output["msg"] == "hello"
        assert output["level"] == "INFO"
        assert output["app"] == "cordfeeder"
        assert "ts" in output
        assert output["ts"].endswith("Z")

    def test_extra_fields_included(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="with extras",
            args=(),
            exc_info=None,
        )
        record.feed_id = 42
        output = json.loads(formatter.format(record))
        assert output["feed_id"] == 42

    def test_exception_stack_is_clean_single_line_json(self):
        """err.stack should be a single string with real newlines (not garbled
        mixed escaping). When json.dumps serialises it, the result should
        contain \\n — never \\\\n."""
        formatter = JSONFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            record = logging.LogRecord(
                name="test",
                level=logging.ERROR,
                pathname="",
                lineno=0,
                msg="failed",
                args=(),
                exc_info=sys.exc_info(),
            )

        raw_json = formatter.format(record)
        output = json.loads(raw_json)
        assert "err.type" in output
        assert output["err.type"] == "ValueError"
        assert output["err.msg"] == "boom"
        # The stack should contain real newlines (not literal backslash-n)
        assert "\n" in output["err.stack"]
        # And the raw JSON should NOT contain \\\\n (double-escaped newlines)
        assert "\\\\n" not in raw_json


class TestConfig:
    def test_missing_token_raises(self, monkeypatch):
        monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
        with pytest.raises(ValueError, match="DISCORD_BOT_TOKEN"):
            Config.from_env()

    def test_invalid_poll_interval_raises(self, monkeypatch):
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
        monkeypatch.setenv("DEFAULT_POLL_INTERVAL", "not-a-number")
        with pytest.raises(
            ValueError, match="DEFAULT_POLL_INTERVAL must be an integer"
        ):
            Config.from_env()

    def test_log_summary_excludes_token(self):
        config = Config(
            discord_token="secret-token-123",
            feed_manager_role="Feed Manager",
            default_poll_interval=900,
            database_path="test.db",
            log_level="INFO",
        )
        summary = config.log_summary()
        assert "secret-token-123" not in str(summary)
        assert "discord_token" not in summary
        assert summary["database_path"] == "test.db"
