"""
Package init for strategic_reports.daily.core.

configure_logging() sets up structlog so every module in this package
produces structured, timestamped log output from the moment the package is
imported; the CLI calls this at startup before doing anything else.
structlog's key=value output (vs. logging.basicConfig()'s plain strings) is
what makes cross-run log aggregation and grepping practical in production.

The rest of this file re-exports public symbols from submodules so callers
can do `from strategic_reports.daily.core import LLMClient, run_pipeline`
without knowing which submodule each lives in. __all__ is the contract for
what's public.
"""

import logging

import structlog


def configure_logging(level: str = "INFO") -> None:
    """
    Configure structlog and the standard library logging to work together.

    Uses stdlib-bound mode (structlog events pass through Python's standard
    logging) so that third-party libraries like litellm, which log via
    stdlib logging.getLogger(), are filtered by the same level.

    getattr(logging, level.upper(), logging.INFO) falls back to INFO rather
    than raising if an invalid level string is passed.
    """
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
    # "%(message)s": don't add stdlib's own prefix — structlog already did it.
    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, level.upper(), logging.INFO),
    )


# Placed after configure_logging() so submodule-level code that calls
# structlog doesn't run before configure_logging() is defined.

from .archive_query import find_relevant_communities
from .article_archive import load_articles, record_articles
from .bullet_diff import append_bullet_run, diff_all_topics, load_bullet_history
from .db import ensure_database_reachable, get_connection, record_run
from .db_status import DbStatusReport, RunGap, RunHealth, db_status_as_dict, load_db_status
from .feed_validation import FeedCheckResult, remove_dead_feeds, validate_topic_feeds
from .ingestion import fetch_topic_articles
from .llm_client import LLMClient
from .models import (
    ArchiveAnswer,
    ArticleSummary,
    ArticleSummaryBatch,
    BulletDiff,
    CommunitySummary,
    CrossTopicSynthesis,
    FeedConfig,
    QueryTags,
    RawArticle,
    StrategicInsight,
    TokenUsage,
    TopicConfig,
    TopicResult,
)
from .overview_archive import record_overview
from .pipeline import (
    answer_archive_question,
    extract_query_tags,
    run_pipeline,
    summarize_communities,
)
from .prompts import (
    SYSTEM_STRATEGIST,
    SYSTEM_SUMMARIZER,
    build_strategy_prompt,
    build_summarize_prompt,
)
from .rdf_export import build_graph as build_rdf_graph
from .rdf_export import export_rdf
from .renderer import render_db_status, render_report
from .systems_signals import (
    LaggedCorrelation,
    tag_rate_lagged_correlations,
    topic_urgency_lagged_correlations,
)
from .tag_graph import (
    build_display_graph,
    build_graph_data,
    find_bridge_tags,
    group_articles_by_community,
    write_tag_graph,
)
from .tag_tracking import (
    EmergingTagAlert,
    check_emerging_tags,
    load_tag_rate_history,
    rebuild_graph_data,
    record_bridge_tags,
    record_community_summaries,
    record_emerging_tag_alerts,
    record_tags,
)
from .urgency import UrgencyAlert, append_run, check_alerts, load_history

__all__ = [
    "configure_logging",
    # Data models
    "ArchiveAnswer",
    "ArticleSummary",
    "ArticleSummaryBatch",
    "BulletDiff",
    "CommunitySummary",
    "CrossTopicSynthesis",
    "FeedConfig",
    "QueryTags",
    "RawArticle",
    "StrategicInsight",
    "TokenUsage",
    "TopicConfig",
    "TopicResult",
    # Pipeline components
    "LLMClient",
    "fetch_topic_articles",
    "FeedCheckResult",
    "validate_topic_feeds",
    "remove_dead_feeds",
    "run_pipeline",
    "summarize_communities",
    "answer_archive_question",
    "extract_query_tags",
    "find_relevant_communities",
    "render_report",
    "build_display_graph",
    "build_graph_data",
    "find_bridge_tags",
    "group_articles_by_community",
    "write_tag_graph",
    "get_connection",
    "ensure_database_reachable",
    "record_run",
    "DbStatusReport",
    "RunGap",
    "RunHealth",
    "load_db_status",
    "db_status_as_dict",
    "render_db_status",
    "load_articles",
    "record_articles",
    "record_overview",
    "append_bullet_run",
    "diff_all_topics",
    "load_bullet_history",
    "UrgencyAlert",
    "append_run",
    "check_alerts",
    "load_history",
    "build_rdf_graph",
    "export_rdf",
    "EmergingTagAlert",
    "check_emerging_tags",
    "load_tag_rate_history",
    "rebuild_graph_data",
    "record_bridge_tags",
    "record_community_summaries",
    "record_emerging_tag_alerts",
    "record_tags",
    "LaggedCorrelation",
    "topic_urgency_lagged_correlations",
    "tag_rate_lagged_correlations",
    # Prompt components
    "SYSTEM_SUMMARIZER",
    "SYSTEM_STRATEGIST",
    "build_summarize_prompt",
    "build_strategy_prompt",
]
