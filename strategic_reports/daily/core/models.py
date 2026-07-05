"""
Data models for the strategic reports pipeline.

Every object that flows through the pipeline has a Pydantic model. This is the
core modernization over the original pipeline, which passed raw dicts, Pandas
DataFrames, and pickle files between stages.

Benefits of Pydantic models here:
  1. Type safety — editors and type checkers catch mistakes at write time.
  2. Validation — invalid data raises a clear ValidationError immediately,
     not a cryptic KeyError three stages later.
  3. Field constraints — min_length/max_length on list fields enforce LLM
     output shape at parse time. This replaces the old approach of writing
     constraints in prompt text and hoping the model obeyed them.
  4. Self-documenting — Field(description=...) doubles as schema documentation
     that instructor can inject into the LLM call to guide output.

Data flow through the pipeline:
  JSON config → FeedConfig / TopicConfig
  RSS feed    → RawArticle
  LLM call 1  → ArticleSummaryBatch (contains list[ArticleSummary])
  LLM call 2  → StrategicInsight
  All of above → TopicResult  (one per topic, returned by the pipeline)
"""

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from .tag_normalizer import normalize_tags


# ---------------------------------------------------------------------------
# Configuration models — describe inputs, not pipeline outputs
# ---------------------------------------------------------------------------

class FeedConfig(BaseModel):
    """One RSS feed source, as stored in the feeds_*.json files."""
    title: str
    url: str


class TopicConfig(BaseModel):
    """
    One topic (e.g. "Artificial Intelligence") with a pointer to its feeds file.

    slug:       matches the feeds JSON filename, e.g. "feeds_ai" → feeds_ai.json
    feeds_file: Path is a Pydantic-supported type; it validates the value is
                path-like and converts strings automatically.
    """
    slug: str
    title: str
    feeds_file: Path


# ---------------------------------------------------------------------------
# Ingestion model — raw content from RSS feeds before LLM processing
# ---------------------------------------------------------------------------

class RawArticle(BaseModel):
    """
    An article retrieved from an RSS feed, before any LLM processing.

    publish_date is a proper datetime (not a string) so we can compare it
    against the hours_cutoff window with arithmetic — no string parsing needed
    downstream.

    summary_from_feed is the RSS feed's own summary excerpt, separate from
    the LLM-generated summary. We keep it but don't use it in prompts because
    it's often just the first sentence and adds noise.
    """
    title: str
    content: str
    link: str
    publish_date: datetime
    summary_from_feed: str = ""  # optional; many feeds omit it


# ---------------------------------------------------------------------------
# LLM output models — structured outputs returned by instructor
# ---------------------------------------------------------------------------

class ArticleSummary(BaseModel):
    """
    LLM-generated summary and tags for one article.

    The Field() constraints (min_length, max_length on list fields) are
    enforced by Pydantic at parse time. When instructor receives an LLM
    response that violates these, it retries the call automatically with
    the validation error fed back as context.

    This is the key improvement over the old pipeline's hopefully_fix_the_response()
    function, which tried to massage the JSON string into shape with regex.

    Note: publish_date is kept as a string here (not datetime) because the LLM
    echoes it back from the prompt as-is, and we only need it for display.
    """
    title: str
    link: str
    publish_date: str
    summary: list[str] = Field(
        # The description is injected into the LLM prompt by instructor as
        # part of the JSON schema, giving the model explicit guidance.
        description="Exactly 3 bullet points summarizing the article. Do not truncate sentences.",
        min_length=3,  # Pydantic rejects fewer than 3 items
        max_length=3,  # Pydantic rejects more than 3 items
    )
    tags: list[str] = Field(
        description=(
            "5 to 20 descriptive tags. Lowercase. Singular nouns. "
            "Spell out all words — no abbreviations or acronyms. "
            "Use spaces not hyphens for multi-word tags. "
            "Use American spelling (e.g. 'defense' not 'defence')."
        ),
        min_length=5,
        max_length=20,
    )

    @field_validator("tags", mode="after")
    @classmethod
    def normalize_tag_list(cls, v: list[str]) -> list[str]:
        return normalize_tags(v)


class ArticleSummaryBatch(BaseModel):
    """
    Wrapper for a batch of ArticleSummary objects.

    instructor needs a top-level model to parse into, and the LLM returns
    all article summaries in one response (up to batch_size at a time).
    This wrapper gives that response a clean container.
    """
    articles: list[ArticleSummary]


class StrategicInsight(BaseModel):
    """
    LLM-generated strategic synthesis for one topic.

    The description in Field() is part of what instructor sends to the LLM
    as schema guidance — it's doing double duty as a prompt instruction and
    a type constraint.
    """
    bullets: list[str] = Field(
        description=(
            "3 to 5 strategic insight bullet points. Each bullet is 1–3 sentences. "
            "Focus on business strategy implications, career positioning, and actionable moves. "
            "Prioritize highest-leverage insights. Do not quote source material directly."
        ),
        min_length=3,
        max_length=5,
    )
    urgency_score: float = Field(
        description=(
            "A score from 0.0 to 1.0 indicating how urgently this topic requires attention TODAY "
            "relative to a typical day in this domain. Most days should score between 0.2 and 0.6. "
            "Scores above 0.75 should be uncommon; above 0.85 should be genuinely rare. "
            "Use the full range — do not cluster near 0.8. "
            "Scale: "
            "0.0–0.15 = nothing happening, purely routine or recycled content; "
            "0.2–0.35 = normal background activity, worth a glance but no action needed; "
            "0.4–0.55 = a few noteworthy developments, monitor but no urgency; "
            "0.6–0.70 = meaningful news requiring attention this week; "
            "0.75–0.84 = significant developments requiring attention in the next day or two "
            "(e.g. a surprise earnings miss, a court ruling, a major product launch); "
            "0.85–0.94 = high-impact event requiring near-immediate attention "
            "(e.g. central bank emergency rate move, unexpected military escalation, "
            "major data breach at a systemically important company); "
            "0.95–1.0 = crisis-level, drop everything "
            "(e.g. market circuit breaker triggered, armed conflict outbreak, "
            "imminent catastrophic regulatory action). "
            "Score relative to domain norms: a routine defense budget update is 0.2 for Defense; "
            "an unexpected troop mobilization is 0.9. A new AI paper is 0.3 for AI; "
            "a surprise frontier model release is 0.75."
        ),
        ge=0.0,
        le=1.0,
    )


class BulletDiff(BaseModel):
    """
    LLM-classified diff between today's and yesterday's strategic bullets for one topic.

    The LLM reads both sets and semantically classifies each bullet — a bullet
    that makes the same point with different wording counts as continued, not new.
    All three lists use text copied from the source bullets (today's for new/continued,
    yesterday's for dropped).
    """
    new: list[str] = Field(
        default_factory=list,
        description=(
            "Today's bullets that represent genuinely new insights not present yesterday. "
            "Copy the exact text from today's bullets. Empty list if nothing is new."
        ),
    )
    continued: list[str] = Field(
        default_factory=list,
        description=(
            "Today's bullets that carry over a theme or insight from yesterday, "
            "possibly with updated details. Copy the exact text from today's bullets. "
            "Empty list if nothing continued."
        ),
    )
    dropped: list[str] = Field(
        default_factory=list,
        description=(
            "Yesterday's bullets that no longer appear in today's output. "
            "Copy the exact text from yesterday's bullets. Empty list if nothing dropped."
        ),
    )


class CrossTopicSynthesis(BaseModel):
    """
    LLM-generated strategic overview that synthesizes insights across all topics.

    This is produced by a single LLM call that reads all per-topic StrategicInsight
    bullets together and identifies overarching themes, cross-domain connections,
    and emergent patterns that are not visible from any single topic alone.
    """
    bullets: list[str] = Field(
        description=(
            "3 to 4 strategic insights that cut across multiple topics. "
            "Each bullet identifies a cross-cutting theme, emergent connection, or pattern "
            "spanning two or more domains — something not visible from any single topic alone. "
            "Be specific about which domains connect. 1–3 sentences each."
        ),
        min_length=3,
        max_length=4,
    )


# ---------------------------------------------------------------------------
# Observability model — tracks API cost across calls
# ---------------------------------------------------------------------------

class TokenUsage(BaseModel):
    """
    Accumulated token counts from one or more LLM calls.

    Making this a Pydantic model (rather than a plain dict or namedtuple)
    means it validates its own fields and can be stored inside TopicResult
    without any special serialization.

    __add__ makes instances addable with the + operator, which lets us
    accumulate usage naturally:
        total = TokenUsage()
        for call_usage in all_usages:
            total = total + call_usage

    Note: __add__ returns a NEW TokenUsage rather than mutating self.
    Immutable value objects are easier to reason about in async code where
    multiple coroutines might reference the same object.
    """
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        # Forward reference "TokenUsage" in quotes because the class is not
        # yet fully defined when the type hint is evaluated at class body time.
        return TokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


# ---------------------------------------------------------------------------
# Result model — one per topic, carries everything out of the pipeline
# ---------------------------------------------------------------------------

class TopicResult(BaseModel):
    """
    The complete output for one topic after the pipeline finishes.

    This is the only object that crosses the boundary between the pipeline
    layer and the rendering layer. Having a single typed container means
    the renderer never has to reach into multiple dictionaries or catch
    KeyErrors — it just reads attributes.

    Three possible states (each renders differently in the HTML templates):
      1. success:  strategy is not None, articles is non-empty, error is None
      2. empty:    strategy is None, articles is empty, error is None
                   (topic ran fine but no recent news was found)
      3. error:    error is a non-None string, strategy is None

    default_factory=list is required instead of a default of [] because
    Python shares mutable default arguments across all instances — a common
    footgun. Pydantic enforces this; it will raise an error if you pass a
    bare [] as a default for a list field.
    """
    config: TopicConfig
    articles: list[ArticleSummary] = Field(default_factory=list)
    strategy: StrategicInsight | None = None       # None until synthesized
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    error: str | None = None                        # set if any stage failed
