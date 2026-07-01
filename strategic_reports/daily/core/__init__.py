"""
Package init for strategic_reports.daily.core.

This file serves two purposes:

1. configure_logging()
   Sets up structlog so that every module in the package (llm_client.py,
   ingestion.py, pipeline.py, etc.) produces structured, timestamped log output
   from the moment the package is imported. The CLI calls this at startup before
   doing anything else.

2. Re-exports
   Making public symbols importable directly from the package:
       from strategic_reports.daily.core import LLMClient, run_pipeline
   instead of requiring callers to know which submodule each lives in:
       from strategic_reports.daily.core.llm_client import LLMClient
       from strategic_reports.daily.core.pipeline import run_pipeline

   The __all__ list is the contract: it says explicitly which names are part
   of the public API. "from core import *" only imports names in __all__.

WHY STRUCTLOG INSTEAD OF print() OR logging.basicConfig()
----------------------------------------------------------
Standard Python logging.basicConfig() produces strings like:
    INFO:core.pipeline:Pipeline started

structlog's ConsoleRenderer produces:
    2026-06-27T14:32:01Z [info     ] pipeline_start   [core.pipeline] topics=12

The key=value pairs in structlog output are:
  - Machine-readable: log aggregation tools (Datadog, Splunk) can index them
  - Grep-able: "grep topic_no_articles app.log" finds all empty-topic events
  - Correlated: you can add {"run_id": run_id} to every log line for a run

HOW STRUCTLOG PROCESSORS WORK
------------------------------
structlog.configure(processors=[...]) defines a pipeline that each log event
passes through in order:

  add_log_level    — adds the "level" field (info, warning, error)
  add_logger_name  — adds the module name that called log.info(...)
  TimeStamper      — adds ISO 8601 timestamp
  ConsoleRenderer  — formats everything as a human-readable line

In production, you'd swap ConsoleRenderer for JSONRenderer to get
machine-parseable {"level": "info", "event": "pipeline_start", "topics": 12}.
"""

import logging
import structlog


def configure_logging(level: str = "INFO") -> None:
    """
    Configure structlog and the standard library logging to work together.

    structlog can operate in two modes:
      - Standalone: structlog handles everything (simpler but less compatible)
      - stdlib-bound: structlog events pass through Python's standard logging
                      (required when third-party libraries like litellm log via
                      stdlib logging.getLogger())

    We use stdlib-bound mode here so that litellm's own log output is filtered
    by the same level as our structlog output.

    The level parameter maps to stdlib constants:
      "DEBUG"   → logging.DEBUG   (10)
      "INFO"    → logging.INFO    (20)
      "WARNING" → logging.WARNING (30)
      "ERROR"   → logging.ERROR   (40)

    getattr(logging, level.upper(), logging.INFO) is a safe way to convert
    the string to the constant — if someone passes "VERBOSE" (invalid),
    it falls back to INFO rather than raising AttributeError.
    """
    structlog.configure(
        processors=[
            # Each processor is a callable(logger, method_name, event_dict) → event_dict.
            # They run in order; the last one must return a string (the final output).
            structlog.stdlib.add_log_level,         # event_dict["level"] = "info"
            structlog.stdlib.add_logger_name,        # event_dict["logger"] = "core.pipeline"
            structlog.processors.TimeStamper(fmt="iso"),  # event_dict["timestamp"] = "2026-..."
            structlog.dev.ConsoleRenderer(),         # formats everything as a readable line
        ],
        wrapper_class=structlog.stdlib.BoundLogger,     # makes log.info(), log.debug() etc. work
        context_class=dict,                              # use plain dicts for event_dict
        logger_factory=structlog.stdlib.LoggerFactory(), # routes to stdlib logging under the hood
    )
    # Configure stdlib logging so its output format matches structlog's output.
    # "%(message)s" means don't add stdlib's own prefix — structlog already did it.
    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, level.upper(), logging.INFO),
    )


# ---------------------------------------------------------------------------
# Re-export public symbols from submodules
# ---------------------------------------------------------------------------
# Imports are placed after configure_logging() because Python evaluates the
# file top-to-bottom. If imports were at the top, submodule-level code that
# calls structlog would run before configure_logging() is defined.

from .models import (
    ArticleSummary,
    ArticleSummaryBatch,
    BulletDiff,
    CrossTopicSynthesis,
    FeedConfig,
    RawArticle,
    StrategicInsight,
    TokenUsage,
    TopicConfig,
    TopicResult,
)
from .llm_client import LLMClient
from .ingestion import fetch_topic_articles
from .pipeline import run_pipeline
from .renderer import render_report
from .tag_graph import write_tag_graph
from .urgency import UrgencyAlert, append_run, check_alerts, load_history
from .bullet_diff import append_bullet_run, diff_all_topics, load_bullet_history
from .prompts import (
    SYSTEM_SUMMARIZER,
    SYSTEM_STRATEGIST,
    build_summarize_prompt,
    build_strategy_prompt,
)

# __all__ defines the public API of this package.
# Only these names are exported by "from core import *".
# It also serves as documentation — a quick inventory of what this package provides.
__all__ = [
    "configure_logging",
    # Data models
    "ArticleSummary",
    "ArticleSummaryBatch",
    "BulletDiff",
    "CrossTopicSynthesis",
    "FeedConfig",
    "RawArticle",
    "StrategicInsight",
    "TokenUsage",
    "TopicConfig",
    "TopicResult",
    # Pipeline components
    "LLMClient",
    "fetch_topic_articles",
    "run_pipeline",
    "render_report",
    "write_tag_graph",
    # Prompt components
    "SYSTEM_SUMMARIZER",
    "SYSTEM_STRATEGIST",
    "build_summarize_prompt",
    "build_strategy_prompt",
]
