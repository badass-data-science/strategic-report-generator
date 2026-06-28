import logging
import structlog


def configure_logging(level: str = "INFO") -> None:
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
    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, level.upper(), logging.INFO),
    )


from .models import (
    ArticleSummary,
    ArticleSummaryBatch,
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
from .prompts import (
    SYSTEM_SUMMARIZER,
    SYSTEM_STRATEGIST,
    build_summarize_prompt,
    build_strategy_prompt,
)

__all__ = [
    "configure_logging",
    "ArticleSummary",
    "ArticleSummaryBatch",
    "FeedConfig",
    "RawArticle",
    "StrategicInsight",
    "TokenUsage",
    "TopicConfig",
    "TopicResult",
    "LLMClient",
    "fetch_topic_articles",
    "run_pipeline",
    "render_report",
    "SYSTEM_SUMMARIZER",
    "SYSTEM_STRATEGIST",
    "build_summarize_prompt",
    "build_strategy_prompt",
]
