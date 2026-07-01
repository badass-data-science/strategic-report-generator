"""
Tests for Pydantic data models (models.py).

WHAT WE'RE TESTING
-------------------
The primary value of these tests is verifying that Pydantic's Field constraints
(min_length, max_length) work as intended. The constraints are what prevent the
pipeline from silently producing malformed output when the LLM returns too few
or too many bullets/tags.

Each test class mirrors one model class. Within a class:
  - test_valid*          : confirm well-formed data is accepted
  - test_too_few_*       : confirm Pydantic raises ValidationError when there's
                           not enough data (min_length violated)
  - test_too_many_*      : confirm Pydantic raises ValidationError when there's
                           too much data (max_length violated)

WHY pytest.raises(ValidationError)?
------------------------------------
pytest.raises() is a context manager that asserts an exception is raised.
If the code inside the with block DOESN'T raise ValidationError, the test FAILS.
This is the correct way to test error conditions — don't catch exceptions yourself.

    with pytest.raises(ValidationError):
        ArticleSummary(...bad data...)
    # If we get here, ArticleSummary accepted the bad data — that's a bug.

PYTEST CLASS GROUPING
----------------------
Grouping tests into classes (class TestArticleSummary:) is optional in pytest,
but useful for:
  1. Organization — all ArticleSummary tests are visually and logically grouped
  2. IDE navigation — you can run all tests in a class at once
  3. No shared state — pytest doesn't reuse class instances between tests (unlike
     unittest.TestCase), so there's no risk of one test affecting another

Note: no __init__ method and no setUp/tearDown — pytest handles setup via fixtures.
"""

import datetime

import pytest
from pydantic import ValidationError

from strategic_reports.daily.core.models import (
    ArticleSummary,
    FeedConfig,
    RawArticle,
    StrategicInsight,
    TokenUsage,
    TopicResult,
)


class TestArticleSummary:
    """Tests for ArticleSummary.summary (exactly 3 bullets) and .tags (5-20)."""

    def test_valid(self):
        """Happy path: 3 bullets, 5 tags — both at their minimums."""
        s = ArticleSummary(
            title="t", link="https://x.com", publish_date="2026-06-27",
            summary=["A.", "B.", "C."],
            tags=["one", "two", "three", "four", "five"],
        )
        assert len(s.summary) == 3
        assert len(s.tags) == 5

    def test_too_few_summary_bullets(self):
        """
        Only 1 bullet — min_length=3 should reject this.
        This is the key validation we need to prevent truncated LLM output from
        silently passing through the pipeline.
        """
        with pytest.raises(ValidationError):
            ArticleSummary(
                title="t", link="l", publish_date="2026-06-27",
                summary=["Only one bullet."],
                tags=["a", "b", "c", "d", "e"],
            )

    def test_too_many_summary_bullets(self):
        """
        4 bullets — max_length=3 should reject this.
        Prevents the LLM from sneaking in extra bullets that inflate the report.
        """
        with pytest.raises(ValidationError):
            ArticleSummary(
                title="t", link="l", publish_date="2026-06-27",
                summary=["A.", "B.", "C.", "D."],
                tags=["a", "b", "c", "d", "e"],
            )

    def test_too_few_tags(self):
        """4 tags — min_length=5 should reject this."""
        with pytest.raises(ValidationError):
            ArticleSummary(
                title="t", link="l", publish_date="2026-06-27",
                summary=["A.", "B.", "C."],
                tags=["only", "four", "tags", "here"],
            )

    def test_too_many_tags(self):
        """21 tags — max_length=20 should reject this."""
        with pytest.raises(ValidationError):
            ArticleSummary(
                title="t", link="l", publish_date="2026-06-27",
                summary=["A.", "B.", "C."],
                # [f"tag{i}" for i in range(21)] generates ["tag0", "tag1", ..., "tag20"]
                tags=[f"tag{i}" for i in range(21)],
            )

    def test_max_tags_accepted(self):
        """20 tags — exactly at the max, should be accepted."""
        s = ArticleSummary(
            title="t", link="l", publish_date="2026-06-27",
            summary=["A.", "B.", "C."],
            tags=[f"tag{i}" for i in range(20)],
        )
        assert len(s.tags) == 20


class TestStrategicInsight:
    """Tests for StrategicInsight.bullets (3-5 bullets)."""

    def test_valid_three_bullets(self):
        """3 bullets — at the minimum."""
        s = StrategicInsight(bullets=["A.", "B.", "C."], urgency_score=0.5)
        assert len(s.bullets) == 3

    def test_valid_five_bullets(self):
        """5 bullets — at the maximum."""
        s = StrategicInsight(bullets=["A.", "B.", "C.", "D.", "E."], urgency_score=0.5)
        assert len(s.bullets) == 5

    def test_too_few_bullets(self):
        """2 bullets — below min_length=3, should be rejected."""
        with pytest.raises(ValidationError):
            StrategicInsight(bullets=["A.", "B."])

    def test_too_many_bullets(self):
        """6 bullets — above max_length=5, should be rejected."""
        with pytest.raises(ValidationError):
            StrategicInsight(bullets=["A.", "B.", "C.", "D.", "E.", "F."])


class TestTokenUsage:
    """
    Tests for TokenUsage addition (the __add__ method).

    TokenUsage.__add__ is critical for the pipeline's cost accounting.
    It accumulates usage bottom-up: each LLM call returns a TokenUsage,
    and the pipeline adds them together into per-topic and then total counts.
    """

    def test_default_zeros(self):
        """A new TokenUsage starts at all zeros."""
        u = TokenUsage()
        assert u.prompt_tokens == 0
        assert u.completion_tokens == 0
        assert u.total_tokens == 0

    def test_addition(self):
        """Basic + operator: fields are summed independently."""
        a = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        b = TokenUsage(prompt_tokens=20, completion_tokens=10, total_tokens=30)
        c = a + b
        assert c.prompt_tokens == 30
        assert c.completion_tokens == 15
        assert c.total_tokens == 45

    def test_addition_with_zero(self):
        """Adding a zeroed TokenUsage is the identity operation."""
        a = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
        result = a + TokenUsage()
        assert result.total_tokens == 150

    def test_accumulation_loop(self):
        """
        Simulates how the pipeline accumulates usage across multiple LLM calls.
        The pattern:
            total = TokenUsage()
            for usage in usages:
                total = total + usage
        must produce the correct sum.
        """
        usages = [TokenUsage(total_tokens=i * 10) for i in range(1, 6)]
        # usages = [10, 20, 30, 40, 50] total_tokens
        total = TokenUsage()
        for u in usages:
            total = total + u
        assert total.total_tokens == 150  # 10+20+30+40+50


class TestTopicResult:
    """
    Tests for TopicResult defaults and state variants.

    TopicResult has three valid states (success / empty / error).
    We verify that the defaults produce the "empty" state, and that
    setting error produces the "error" state without needing strategy.
    """

    def test_defaults(self, sample_topic_config):
        """
        A minimal TopicResult (only config provided) should have safe defaults.
        This is the "empty" state: topic ran without error but found no articles.
        """
        r = TopicResult(config=sample_topic_config)
        assert r.articles == []           # default_factory=list gives []
        assert r.strategy is None         # no synthesis without articles
        assert r.error is None            # not an error — just no news
        assert r.token_usage.total_tokens == 0  # default_factory=TokenUsage

    def test_error_result(self, sample_topic_config):
        """An error result has error set but strategy stays None."""
        r = TopicResult(config=sample_topic_config, error="Feed not found")
        assert r.error == "Feed not found"
        assert r.strategy is None


class TestFeedConfig:
    """Basic smoke test for FeedConfig — it's a simple model with no constraints."""

    def test_valid(self):
        f = FeedConfig(title="AI News", url="https://example.com/feed")
        assert f.title == "AI News"


class TestRawArticle:
    """
    Tests for RawArticle — verifies the optional summary_from_feed default.

    summary_from_feed has a default of "" because many RSS feeds omit the
    summary field. If it weren't optional, every test and every feed entry
    without a summary would require an empty string explicitly.
    """

    def test_valid(self):
        a = RawArticle(
            title="Test",
            content="Body",
            link="https://example.com",
            publish_date=datetime.datetime(2026, 6, 27, 9, 0),
        )
        # summary_from_feed defaults to "" when not provided
        assert a.summary_from_feed == ""
