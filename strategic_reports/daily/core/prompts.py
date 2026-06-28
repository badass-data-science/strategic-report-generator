"""
Prompt layer: system messages + user-message builders.

System messages define the LLM's role and standing constraints.
Builder functions format typed data into the user turn.

Structured output constraints (bullet counts, tag counts) are enforced by
Pydantic on the response side, so they are omitted here — keeping prompts
focused on task framing, not schema instructions.
"""

from .models import ArticleSummary, RawArticle

# ---------------------------------------------------------------------------
# System messages
# ---------------------------------------------------------------------------

SYSTEM_SUMMARIZER = """\
You are an expert at reading news articles and extracting their key points.
For every article you receive, produce a concise 3-bullet summary and a set
of descriptive tags. Spell out all words in tags — no abbreviations.\
"""

SYSTEM_STRATEGIST = """\
You are an expert strategic analyst specializing in business development,
entrepreneurship, and career strategy for senior technical professionals.
You read article summaries and extract the highest-leverage strategic
insights — opportunities, threats, and actionable positioning moves.
Do not quote source material directly; abstract and generalize.\
"""


# ---------------------------------------------------------------------------
# User-message builders
# ---------------------------------------------------------------------------

def build_summarize_prompt(articles: list[RawArticle]) -> str:
    """Format a batch of articles into a summarization request."""
    parts = [
        f"Summarize and tag each of the {len(articles)} articles below.\n"
    ]
    for i, article in enumerate(articles, 1):
        parts.append(
            f"--- ARTICLE {i} ---\n"
            f"Title: {article.title}\n"
            f"URL: {article.link}\n"
            f"Published: {article.publish_date.isoformat()}\n\n"
            f"{article.content}\n"
        )
    return "\n".join(parts)


def build_strategy_prompt(topic_title: str, summaries: list[ArticleSummary]) -> str:
    """Format article summaries into a strategic synthesis request."""
    parts = [
        f"Based on the following {len(summaries)} article summaries about "
        f"**{topic_title}**, identify the 3–5 most important strategic insights.\n"
    ]
    for summary in summaries:
        bullets = "\n".join(f"  - {b}" for b in summary.summary)
        tags = ", ".join(summary.tags)
        parts.append(
            f"### {summary.title}\n"
            f"Published: {summary.publish_date}\n"
            f"{bullets}\n"
            f"Tags: {tags}\n"
        )
    return "\n".join(parts)
