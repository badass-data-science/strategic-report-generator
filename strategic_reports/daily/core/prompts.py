"""
Prompt layer: system messages + user-message builder functions.

DESIGN PATTERN — system/user split
-----------------------------------
Modern LLM APIs use a two-turn message structure for each call:

  system turn  — persistent role/persona description; sets "who the model is"
  user turn    — the specific request for this call

Keeping them separate is better than concatenating them into one string:
  - The system turn is cached by providers like Claude (prompt caching),
    reducing cost when you call the same system prompt repeatedly.
  - It matches what the model was trained to expect and improves reliability.
  - It's easy to swap out system prompts for A/B testing.

DESIGN PATTERN — constraints in Pydantic, not in prompts
----------------------------------------------------------
The original pipeline embedded schema instructions in the prompt:
  "Please respond in correct, validated JSON format with the schema: {...}"
  "Output MUST contain ONLY 3-5 bullet points."

Those are fragile: the model can ignore or misunderstand them.

In this refactor, structural constraints live in Pydantic Field definitions
(min_length, max_length on list fields in models.py). The model gets the
schema via instructor's automatic JSON schema injection. Prompts focus on
WHAT to do, not HOW to format the response.

DESIGN PATTERN — builder functions over template strings
---------------------------------------------------------
Builder functions take typed inputs and return formatted strings. This makes
them unit-testable (see test_prompts.py) — you can verify that a prompt
contains the right article titles, URLs, and counts without calling an LLM.

A plain f-string template would require string formatting at call time with
no structure, making it hard to add per-article formatting logic later.
"""

from .models import ArticleSummary, RawArticle

# ---------------------------------------------------------------------------
# System messages — define the LLM's role for each stage
# ---------------------------------------------------------------------------

# The backslash at the end of the opening triple-quote prevents a leading
# newline in the string, which would waste tokens and look odd in logs.
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
# User-message builders — format typed data into the user turn
# ---------------------------------------------------------------------------

def build_summarize_prompt(articles: list[RawArticle]) -> str:
    """
    Format a batch of articles into the user message for summarization.

    Each article gets a clear delimiter (--- ARTICLE N ---) that makes it
    easy for the model to distinguish article boundaries, especially in large
    batches. Plain text with structure is more reliable than dumping raw JSON
    at the model, because the model was trained on human-written text, not
    on JSON blobs as input.

    We include title, URL, and publish date because:
    - title / URL are passed through to ArticleSummary (the model echoes them back)
    - publish date helps the model contextualize time-sensitive content
    """
    # Start with a count so the model knows how many articles to process.
    # This helps it avoid truncating the batch early.
    parts = [
        f"Summarize and tag each of the {len(articles)} articles below.\n"
    ]
    for i, article in enumerate(articles, 1):  # enumerate(start=1) gives 1-based numbering
        parts.append(
            f"--- ARTICLE {i} ---\n"
            f"Title: {article.title}\n"
            f"URL: {article.link}\n"
            # isoformat() gives "2026-06-27T10:00:00" — unambiguous and standard
            f"Published: {article.publish_date.isoformat()}\n\n"
            f"{article.content}\n"
        )
    # "\n".join(parts) is more efficient than repeated += on strings,
    # because strings are immutable in Python — += creates a new string each time.
    return "\n".join(parts)


def build_strategy_prompt(topic_title: str, summaries: list[ArticleSummary]) -> str:
    """
    Format article summaries into the user message for strategic synthesis.

    This is the second LLM call in the pipeline: the model has already
    summarized individual articles; now it reads all summaries for a topic
    and derives overarching strategic insights.

    We use Markdown formatting (###, bullet dashes) because models tend to
    produce better-structured output when the input has clear structure.
    The tags are included because they help the model understand thematic
    clusters across articles even when it doesn't re-read the full content.
    """
    parts = [
        f"Based on the following {len(summaries)} article summaries about "
        f"**{topic_title}**, identify the 3–5 most important strategic insights.\n"
    ]
    for summary in summaries:
        # Format each bullet point with indented dash to distinguish them
        # from potential bullet points in the surrounding prompt text.
        bullets = "\n".join(f"  - {b}" for b in summary.summary)
        tags = ", ".join(summary.tags)
        parts.append(
            f"### {summary.title}\n"
            f"Published: {summary.publish_date}\n"
            f"{bullets}\n"
            f"Tags: {tags}\n"
        )
    return "\n".join(parts)
