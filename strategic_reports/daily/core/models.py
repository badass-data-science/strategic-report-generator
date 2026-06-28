from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field


class FeedConfig(BaseModel):
    title: str
    url: str


class TopicConfig(BaseModel):
    slug: str
    title: str
    feeds_file: Path


class RawArticle(BaseModel):
    title: str
    content: str
    link: str
    publish_date: datetime
    summary_from_feed: str = ""


class ArticleSummary(BaseModel):
    title: str
    link: str
    publish_date: str
    summary: list[str] = Field(
        description="Exactly 3 bullet points summarizing the article. Do not truncate sentences.",
        min_length=3,
        max_length=3,
    )
    tags: list[str] = Field(
        description="5 to 20 descriptive tags. Spell words out; no abbreviations.",
        min_length=5,
        max_length=20,
    )


class ArticleSummaryBatch(BaseModel):
    """Structured output for a batch of article summaries."""
    articles: list[ArticleSummary]


class StrategicInsight(BaseModel):
    """Structured output for the per-topic strategic synthesis."""
    bullets: list[str] = Field(
        description=(
            "3 to 5 strategic insight bullet points. Each bullet is 1–3 sentences. "
            "Focus on business strategy implications, career positioning, and actionable moves. "
            "Prioritize highest-leverage insights. Do not quote source material directly."
        ),
        min_length=3,
        max_length=5,
    )


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


class TopicResult(BaseModel):
    config: TopicConfig
    articles: list[ArticleSummary] = Field(default_factory=list)
    strategy: StrategicInsight | None = None
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    error: str | None = None
