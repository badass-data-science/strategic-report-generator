# AGENTS.md

Guidance for AI coding agents working in this repository. See `README.md` for
the full pipeline design and user-facing docs — this file is the quick
orientation for making changes.

## What this is

A daily briefing pipeline: fetches RSS across 12 topics, summarizes and
synthesizes strategic recommendations via an LLM (provider-agnostic via
litellm), renders an HTML report + tag co-occurrence graph, tracks urgency
scores and strategic bullets across runs in a SQLite database, and
optionally uploads the report via SCP/SSH. Two entry points, kept at
feature parity with each other:

- `python -m strategic_reports.daily.cli` — run once, manually
- `flows/daily_report.py` — the same pipeline as a Prefect flow, scheduled
  daily via cron; adds only the optional remote-upload step, which the CLI
  doesn't do

If you add a pipeline step to one entry point (cross-topic synthesis,
urgency alerts, bullet diffing, tag graph), add it to the other too unless
told otherwise — this parity was an explicit, deliberate decision.

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

- 128 tests across `tests/test_*.py`, no real network or LLM calls, runs in
  under a second. CI (`.github/workflows/tests.yml`) runs the same suite on
  every push/PR to `main`, no credentials needed there either.
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
    urgency.py          Urgency alerting: absolute threshold + z-score (SQLite-backed)
    bullet_diff.py      Historical diffing vs. yesterday's bullets (SQLite-backed)
    db.py               SQLite tracking db: schema, connection helper, output_dir/db_path safety guard, run registration
    tag_tracking.py     Per-run tag-graph persistence (linked to run_id) + emerging-tag z-score (SQLite-backed)
    tracing.py          Langfuse / Phoenix instrumentation (opt-in)
  templates/            Jinja2 templates (base, index, topic)
  cli.py                typer CLI entrypoint
  config/topic_order.py Ordered topic slugs + display titles
flows/daily_report.py   Prefect flow (9 tasks) for scheduled runs
data/rss_feeds/         One JSON file per topic listing feed URLs
tests/                  Per-module test files + conftest.py fixtures
LICENSE                 MIT
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
  new options rather than inventing a new config mechanism. Exceptions:
  `--output-dir` and `--db-path` are required (no default, no env var) on
  both entry points — deliberate, not an oversight.
- **`--output-dir` is deleted and recreated on every run** (see
  `render_report()`). Never assume anything written there survives past the
  current run, and never point persistent state at a path inside it.
- **`--db-path` (SQLite tracking db) must never resolve inside
  `--output-dir`.** `db.ensure_safe_db_path()` enforces this at startup on
  both entry points — call it before any real work if you add a third entry
  point that touches the tracking db.
- **Tracking-db functions take `db_path: Path`, not a live `sqlite3.Connection`.**
  Each call (`load_history`, `append_run`, `load_bullet_history`,
  `append_bullet_run`, `record_run`, `record_tags`, `load_tag_rate_history`,
  `rebuild_graph_data`) opens and closes its own short connection. This is
  deliberate: a `sqlite3.Connection` isn't picklable, so a shared one can't
  safely cross a Prefect task boundary.
- **Tag rates, not raw counts, are what gets compared across runs**
  (`tag_tracking.check_emerging_tags`) — a raw tag count means something
  different on a big-news-volume day than a quiet one. There's no absolute-
  threshold fallback for thin-history tags the way `urgency.check_alerts`
  has one for topics: tags are an open, growing vocabulary, so a tag with
  fewer than 7 prior runs (including a brand-new tag) is silently skipped
  rather than guessed at. Don't add one without discussing the threshold —
  it was a deliberate omission, not an oversight.
- **Every tracking-db row gets its own timestamp**, in addition to `run_id` —
  not just a `date`. Needed for precise ordering/change-tracking queries
  (z-score baselines, future drift detection), not just row-insertion order.
  Nothing in the tracking db is pruned (unlike the JSON files it replaced,
  which capped bullet history at 7 entries) — full history is intentional.
- `db.record_run(db_path, run_id, article_count)` must be called once per
  run, before `append_run`/`append_bullet_run` (those insert rows that
  reference `run_id` as a foreign key). `article_count` is the total
  articles considered that run — the denominator for comparing tag/urgency
  weights across runs of different sizes.

## Git identity

This repo's local commits and pushes use the `badass-data-science` git
identity (see `git config --local user.name`), pushed via the SSH host alias
`github-badass`. Don't change this without the user's explicit say-so.
