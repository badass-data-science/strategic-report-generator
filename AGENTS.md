# AGENTS.md

Guidance for AI coding agents working in this repository. See `README.md` for
the full pipeline design and user-facing docs — this file is the quick
orientation for making changes.

## What this is

A daily briefing pipeline: fetches RSS across 12 topics, summarizes and
synthesizes strategic recommendations via an LLM (provider-agnostic via
litellm), renders an HTML report, and optionally uploads it via SCP/SSH.
Scheduled with Prefect (`flows/daily_report.py`).

## Setup

```bash
pip install -r requirements.txt
```

No API keys are required to run the test suite — the LLM client is fully
mocked in tests. Running the actual pipeline requires `LLM_MODEL` and the
matching provider credentials (see README's Quick Start).

## Tests

```bash
pytest
```

- 92 tests across `tests/test_*.py`, no real network or LLM calls, runs in
  under a second.
- `pytest.ini` sets `asyncio_mode = auto` — async test functions don't need
  `@pytest.mark.asyncio`.
- Run the whole suite after any change to `strategic_reports/daily/core/*`;
  it's fast enough that there's no reason to scope it down.

There is no configured linter or formatter in this repo — match the existing
style in the file you're editing rather than introducing a new tool.

## Code layout

```
strategic_reports/daily/
  core/
    models.py          Pydantic models: RawArticle → ArticleSummary → TopicResult → CrossTopicSynthesis → BulletDiff
    llm_client.py       Async LLMClient: litellm + instructor + tenacity retry
    ingestion.py        Async RSS fetching
    prompts.py          System messages + user-message builders
    pipeline.py         Two-phase async orchestrator + cross-topic synthesis
    renderer.py         Jinja2 HTML rendering
    tag_normalizer.py   Tag synonym map, normalize_tags() (Pydantic validator)
    tag_graph.py        Tag co-occurrence graph + Louvain community detection
    urgency.py          Urgency alerting: absolute threshold + z-score
    bullet_diff.py      Historical diffing vs. yesterday's bullets
    tracing.py          Langfuse / Phoenix instrumentation (opt-in)
  templates/            Jinja2 templates (base, index, topic)
  cli.py                typer CLI entrypoint
  config/topic_order.py Ordered topic slugs + display titles
flows/daily_report.py   Prefect flow (8 tasks) for scheduled runs
data/rss_feeds/         One JSON file per topic listing feed URLs
tests/                  Per-module test files + conftest.py fixtures
```

## Conventions to preserve

- **Structured outputs via instructor + Pydantic**, not manual JSON parsing.
  Field constraints (bullet counts, tag counts) belong on the Pydantic model,
  not enforced ad hoc in calling code.
- **Provider-agnostic LLM calls.** Never hardcode a provider-specific client;
  everything goes through `llm_client.py`'s litellm-backed `LLMClient` with a
  model string.
- **Per-topic error isolation.** A failure in one topic's pipeline must
  produce a `TopicResult(error=...)` for that topic only — never let one bad
  feed or LLM timeout abort the other topics. Preserve this in
  `pipeline.py` if touched.
- **Async I/O boundaries.** RSS fetching is concurrent via `asyncio.gather`;
  LLM calls are throttled via an `asyncio.Semaphore` (not `time.sleep`).
  `feedparser` is sync and must stay wrapped in `asyncio.to_thread`.
- Config values follow the CLI-flag + env-var + default pattern already used
  throughout `cli.py` and `flows/daily_report.py` — extend that pattern for
  new options rather than inventing a new config mechanism.

## Git identity

This repo's local commits and pushes use the `badass-data-science` git
identity (see `git config --local user.name`), pushed via the SSH host alias
`github-badass`. Don't change this without the user's explicit say-so.
