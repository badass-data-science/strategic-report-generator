"""Tests for Pydantic data models."""

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
    def test_valid(self):
        s = ArticleSummary(
            title="t", link="https://x.com", publish_date="2026-06-27",
            summary=["A.", "B.", "C."],
            tags=["one", "two", "three", "four", "five"],
        )
        assert len(s.summary) == 3
        assert len(s.tags) == 5

    def test_too_few_summary_bullets(self):
        with pytest.raises(ValidationError):
            ArticleSummary(
                title="t", link="l", publish_date="2026-06-27",
                summary=["Only one bullet."],
                tags=["a", "b", "c", "d", "e"],
            )

    def test_too_many_summary_bullets(self):
        with pytest.raises(ValidationError):
            ArticleSummary(
                title="t", link="l", publish_date="2026-06-27",
                summary=["A.", "B.", "C.", "D."],
                tags=["a", "b", "c", "d", "e"],
            )

    def test_too_few_tags(self):
        with pytest.raises(ValidationError):
            ArticleSummary(
                title="t", link="l", publish_date="2026-06-27",
                summary=["A.", "B.", "C."],
                tags=["only", "four", "tags", "here"],
            )

    def test_too_many_tags(self):
        with pytest.raises(ValidationError):
            ArticleSummary(
                title="t", link="l", publish_date="2026-06-27",
                summary=["A.", "B.", "C."],
                tags=[f"tag{i}" for i in range(21)],
            )

    def test_max_tags_accepted(self):
        s = ArticleSummary(
            title="t", link="l", publish_date="2026-06-27",
            summary=["A.", "B.", "C."],
            tags=[f"tag{i}" for i in range(20)],
        )
        assert len(s.tags) == 20


class TestStrategicInsight:
    def test_valid_three_bullets(self):
        s = StrategicInsight(bullets=["A.", "B.", "C."])
        assert len(s.bullets) == 3

    def test_valid_five_bullets(self):
        s = StrategicInsight(bullets=["A.", "B.", "C.", "D.", "E."])
        assert len(s.bullets) == 5

    def test_too_few_bullets(self):
        with pytest.raises(ValidationError):
            StrategicInsight(bullets=["A.", "B."])

    def test_too_many_bullets(self):
        with pytest.raises(ValidationError):
            StrategicInsight(bullets=["A.", "B.", "C.", "D.", "E.", "F."])


class TestTokenUsage:
    def test_default_zeros(self):
        u = TokenUsage()
        assert u.prompt_tokens == 0
        assert u.completion_tokens == 0
        assert u.total_tokens == 0

    def test_addition(self):
        a = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        b = TokenUsage(prompt_tokens=20, completion_tokens=10, total_tokens=30)
        c = a + b
        assert c.prompt_tokens == 30
        assert c.completion_tokens == 15
        assert c.total_tokens == 45

    def test_addition_with_zero(self):
        a = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
        result = a + TokenUsage()
        assert result.total_tokens == 150

    def test_accumulation_loop(self):
        usages = [TokenUsage(total_tokens=i * 10) for i in range(1, 6)]
        total = TokenUsage()
        for u in usages:
            total = total + u
        assert total.total_tokens == 150  # 10+20+30+40+50


class TestTopicResult:
    def test_defaults(self, sample_topic_config):
        r = TopicResult(config=sample_topic_config)
        assert r.articles == []
        assert r.strategy is None
        assert r.error is None
        assert r.token_usage.total_tokens == 0

    def test_error_result(self, sample_topic_config):
        r = TopicResult(config=sample_topic_config, error="Feed not found")
        assert r.error == "Feed not found"
        assert r.strategy is None


class TestFeedConfig:
    def test_valid(self):
        f = FeedConfig(title="AI News", url="https://example.com/feed")
        assert f.title == "AI News"


class TestRawArticle:
    def test_valid(self):
        a = RawArticle(
            title="Test",
            content="Body",
            link="https://example.com",
            publish_date=datetime.datetime(2026, 6, 27, 9, 0),
        )
        assert a.summary_from_feed == ""
