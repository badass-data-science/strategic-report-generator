"""
Tests for RSS feed health checks (feed_validation.py) — feedparser is mocked
via patch(), same approach as test_ingestion.py (see that file's module
docstring for why, and why the patch target is
"strategic_reports.daily.core.feed_validation.feedparser.parse" rather than
"feedparser.parse").

conftest.py's make_parsed_feed() only sets .entries — it doesn't set
.status/.bozo/.get(), which _check_one_feed also reads. So this file has its
own make_parsed() helper that sets all four.
"""

import json
import socket
from pathlib import Path
from unittest.mock import MagicMock, patch

from strategic_reports.daily.core.feed_validation import (
    _FEED_TIMEOUT_S,
    FeedCheckResult,
    _check_one_feed,
    _clean_exception,
    remove_dead_feeds,
    validate_topic_feeds,
)
from strategic_reports.daily.core.models import FeedConfig, TopicConfig

FEED = FeedConfig(title="Test Feed", url="https://example.com/feed")


def make_parsed(
    status: int | None = 200,
    bozo: bool = False,
    entries: list[MagicMock] | None = None,
    bozo_exception: str | None = None,
) -> MagicMock:
    """Mimic a feedparser parsed-feed result with the fields _check_one_feed reads."""
    parsed = MagicMock()
    parsed.status = status
    parsed.bozo = bozo
    parsed.entries = entries or []
    parsed.get = lambda key, default=None: bozo_exception if key == "bozo_exception" else default
    return parsed


class TestCleanException:
    """Tests for _clean_exception() — strips wrapper noise from raw exception strings."""

    def test_strips_urlopen_wrapper(self) -> None:
        raw = "<urlopen error [Errno -2] Name or service not known>"
        assert _clean_exception(raw) == "[Errno -2] Name or service not known"

    def test_strips_unknown_xml_prefix(self) -> None:
        raw = "<unknown>:2:0: syntax error"
        assert _clean_exception(raw) == "malformed XML (2:0: syntax error)"

    def test_passes_through_unrecognized_format(self) -> None:
        raw = "text/html; charset=utf-8 is not an XML media type"
        assert _clean_exception(raw) == raw


class TestCheckOneFeed:
    """Tests for _check_one_feed() — the per-feed coroutine."""

    async def test_ok_feed(self) -> None:
        parsed = make_parsed(status=200, bozo=False, entries=[MagicMock()])
        with patch(
            "strategic_reports.daily.core.feed_validation.feedparser.parse",
            return_value=parsed,
        ):
            result = await _check_one_feed(FEED)

        assert result.ok is True
        assert result.detail == ""

    async def test_http_200_with_no_entries(self) -> None:
        parsed = make_parsed(status=200, bozo=False, entries=[])
        with patch(
            "strategic_reports.daily.core.feed_validation.feedparser.parse",
            return_value=parsed,
        ):
            result = await _check_one_feed(FEED)

        assert result.ok is False
        assert result.detail == "HTTP 200 but no parseable entries"

    async def test_bozo_with_http_status(self) -> None:
        parsed = make_parsed(
            status=404, bozo=True, entries=[], bozo_exception="<unknown>:2:0: syntax error"
        )
        with patch(
            "strategic_reports.daily.core.feed_validation.feedparser.parse",
            return_value=parsed,
        ):
            result = await _check_one_feed(FEED)

        assert result.ok is False
        assert result.detail == "HTTP 404; malformed XML (2:0: syntax error)"

    async def test_bozo_with_no_http_status(self) -> None:
        """A DNS-level failure never reaches HTTP, so status stays None — no 'HTTP None;' prefix."""
        parsed = make_parsed(
            status=None,
            bozo=True,
            entries=[],
            bozo_exception="<urlopen error [Errno -2] Name or service not known>",
        )
        with patch(
            "strategic_reports.daily.core.feed_validation.feedparser.parse",
            return_value=parsed,
        ):
            result = await _check_one_feed(FEED)

        assert result.ok is False
        assert result.detail == "[Errno -2] Name or service not known"

    async def test_bozo_but_has_entries_counts_as_ok(self) -> None:
        """A feed with minor XML quirks that still parsed usable entries isn't dead."""
        parsed = make_parsed(status=200, bozo=True, entries=[MagicMock()])
        with patch(
            "strategic_reports.daily.core.feed_validation.feedparser.parse",
            return_value=parsed,
        ):
            result = await _check_one_feed(FEED)

        assert result.ok is True

    async def test_feedparser_exception(self) -> None:
        with patch(
            "strategic_reports.daily.core.feed_validation.feedparser.parse",
            side_effect=Exception("The read operation timed out"),
        ):
            result = await _check_one_feed(FEED)

        assert result.ok is False
        assert result.detail == "The read operation timed out"

    async def test_sets_socket_timeout_before_fetching(self) -> None:
        """
        A feed that accepts the connection but never responds must not hang
        _check_one_feed forever — feedparser (via urllib) only honors a
        timeout if socket.setdefaulttimeout() has been set, since it's given
        no explicit per-call timeout.

        Restores the prior default afterward so this test doesn't leak global
        socket state into whichever test runs next.
        """
        original = socket.getdefaulttimeout()
        socket.setdefaulttimeout(None)
        try:
            parsed = make_parsed(status=200, bozo=False, entries=[MagicMock()])
            with patch(
                "strategic_reports.daily.core.feed_validation.feedparser.parse",
                return_value=parsed,
            ):
                await _check_one_feed(FEED)

            assert socket.getdefaulttimeout() == _FEED_TIMEOUT_S
        finally:
            socket.setdefaulttimeout(original)


class TestValidateTopicFeeds:
    """Tests for validate_topic_feeds() — the public multi-feed entry point."""

    async def test_checks_every_feed_in_file(self, sample_topic_config: TopicConfig) -> None:
        parsed = make_parsed(status=200, bozo=False, entries=[MagicMock()])
        with patch(
            "strategic_reports.daily.core.feed_validation.feedparser.parse",
            return_value=parsed,
        ):
            results = await validate_topic_feeds(sample_topic_config)

        assert len(results) == 2
        assert all(r.ok for r in results)


class TestRemoveDeadFeeds:
    """Tests for remove_dead_feeds() — pruning feeds_*.json and logging REMOVED.json."""

    def _topic(self, tmp_path: Path) -> TopicConfig:
        feeds_file = tmp_path / "feeds_test.json"
        feeds_file.write_text(json.dumps({
            "feeds": [
                {"title": "Good Feed", "url": "https://good.example.com/feed"},
                {"title": "Dead Feed", "url": "https://dead.example.com/feed"},
            ]
        }))
        return TopicConfig(slug="feeds_test", title="Test", feeds_file=feeds_file)

    def test_prunes_dead_feed_and_logs_it(self, tmp_path: Path) -> None:
        topic = self._topic(tmp_path)
        removed_path = tmp_path / "REMOVED.json"
        removed_path.write_text(json.dumps({"removed": {}}))

        # Build results by hand rather than hitting the network.
        feeds = json.loads(topic.feeds_file.read_text())["feeds"]
        results = [
            FeedCheckResult(feed=FeedConfig(**feeds[0]), ok=True),
            FeedCheckResult(feed=FeedConfig(**feeds[1]), ok=False, detail="Connection refused"),
        ]

        removed_count = remove_dead_feeds(topic, results, removed_path)

        assert removed_count == 1

        remaining = json.loads(topic.feeds_file.read_text())["feeds"]
        assert len(remaining) == 1
        assert remaining[0]["title"] == "Good Feed"

        removed_data = json.loads(removed_path.read_text())
        assert removed_data["removed"]["feeds_test"] == [
            {
                "title": "Dead Feed",
                "url": "https://dead.example.com/feed",
                "exception": "Connection refused",
            }
        ]

    def test_appends_to_existing_removed_entries(self, tmp_path: Path) -> None:
        topic = self._topic(tmp_path)
        removed_path = tmp_path / "REMOVED.json"
        removed_path.write_text(json.dumps({
            "removed": {
                "feeds_test": [
                    {"title": "Old Removal", "url": "https://old.example.com", "reason": "manual"}
                ]
            }
        }))

        feeds = json.loads(topic.feeds_file.read_text())["feeds"]
        results = [
            FeedCheckResult(feed=FeedConfig(**feeds[0]), ok=True),
            FeedCheckResult(feed=FeedConfig(**feeds[1]), ok=False, detail="timed out"),
        ]

        remove_dead_feeds(topic, results, removed_path)

        removed_data = json.loads(removed_path.read_text())
        assert len(removed_data["removed"]["feeds_test"]) == 2
        assert removed_data["removed"]["feeds_test"][0]["title"] == "Old Removal"
        assert removed_data["removed"]["feeds_test"][1]["title"] == "Dead Feed"

    def test_no_dead_feeds_leaves_files_untouched(self, tmp_path: Path) -> None:
        topic = self._topic(tmp_path)
        removed_path = tmp_path / "REMOVED.json"
        removed_path.write_text(json.dumps({"removed": {}}))
        original_feeds_content = topic.feeds_file.read_text()
        original_removed_content = removed_path.read_text()

        feeds = json.loads(topic.feeds_file.read_text())["feeds"]
        results = [FeedCheckResult(feed=FeedConfig(**f), ok=True) for f in feeds]

        removed_count = remove_dead_feeds(topic, results, removed_path)

        assert removed_count == 0
        assert topic.feeds_file.read_text() == original_feeds_content
        assert removed_path.read_text() == original_removed_content
