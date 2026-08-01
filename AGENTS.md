# AGENTS.md

Guidance for AI coding agents working in this repository. See `README.md` for
the full pipeline design and user-facing docs — this file is the quick
orientation for making changes.

## What this is

A daily briefing pipeline: fetches RSS across 12 topics, summarizes and
synthesizes strategic recommendations via an LLM (provider-agnostic via
litellm), renders an HTML report + tag co-occurrence graph, and tracks
urgency scores/strategic bullets/article summaries/community summaries
across runs in a SQLite database. Two scheduled/batch entry points, kept
at full feature parity with each other:

- `python -m strategic_reports.daily.cli run` — the batch pipeline, run once, manually
- `strategic_reports/daily/flows/daily_report.py` — the same pipeline as a
  Prefect flow, scheduled daily via cron

If you add a pipeline step to one entry point (cross-topic synthesis,
urgency alerts, bullet diffing, tag graph, community summaries), add it to
the other too unless told otherwise — this parity was an explicit,
deliberate decision.

A second command, `python -m strategic_reports.daily.cli ask "<question>"`,
is a deliberate exception: an interactive, human-in-the-loop archive
query (graph-guided retrieval over `community_summaries` — see
`archive_query.py`), not a scheduled batch step. It has no Prefect
equivalent, and that's intentional, not a parity gap to fix.

A third command, `python -m strategic_reports.daily.cli export-rdf`, is
the same kind of exception: an on-demand export of the tracking database
to RDF/Turtle (see `rdf_export.py`), not a pipeline step. It complements
`tag_graph.py`'s per-run co-occurrence JSON/HTML — it does not replace or
recompute it, and touching one should not require touching the other.

`export-rdf` also has a Prefect flow (`flows/export_rdf_flow.py`) — a
separate file from `daily_report.py`, not a task folded into it, since
`export-rdf` is deliberately independent of the daily pipeline. It's
scheduled as **its own separate process** (own `.serve()` call, own
systemd unit — see README's "Scheduling with Prefect"), not folded into
`daily_report.py`'s `serve()`, so a crash/restart of one never touches
the other. Runs daily at 04:00 America/Los_Angeles (well after
`daily_report_flow`'s 00:30 run) and always does a full rebuild — no
`--since` watermark tracking, matching the CLI command's scope. The
scheduled deployment's `db_path` reads from the same tracking database
`daily_report_flow`'s deployment writes to (both anchored via
`Path.home()`, not cwd). This module no longer exposes a typer/CLI-args
entry point for ad-hoc runs — that capability is superseded by `prefect
deployment run 'export-rdf/export-rdf' --param since=...` now that a
deployment exists; `cli.py export-rdf` remains available for genuinely
Prefect-independent ad-hoc use.

**CLI invocation shape**: `cli.py` now has three commands (`run`, `ask`,
`export-rdf`), so naming one explicitly is required (`... cli.py run
--output-dir ...`) — typer's single-command auto-invoke shorthand (bare
`... cli.py --output-dir ...`) only applies when there's exactly one
command, and no longer applies here. If you ever reduce back to one
command, that shorthand returns; don't assume it's available with two or
more.

## Setup

```bash
pip install -e ".[flow,test]"
```

Package metadata, dependencies, and extras (`flow` for Prefect, `test` for
pytest) all live in `pyproject.toml` — there is no `requirements.txt`.
`data/rss_feeds/*.json` and `flows/` ship as package data inside
`strategic_reports/daily/` (see `paths.py`'s `default_data_dir()`), so the
package is self-contained even though it isn't published to PyPI.

No API keys are required to run the test suite — the LLM client is fully
mocked in tests. Running the actual pipeline requires `LLM_MODEL` and the
matching provider credentials (see README's Quick Start).

## Tests

```bash
pytest
```

- 204 tests across `tests/test_*.py`, no real network or LLM calls, runs in
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
strategic_reports/
  py.typed                 PEP 561 marker
  daily/
    core/
      models.py          Pydantic models: RawArticle → ArticleSummary → TopicResult → CrossTopicSynthesis → BulletDiff
      llm_client.py       Async LLMClient: litellm + instructor + tenacity retry
      ingestion.py        Async RSS fetching
      prompts.py          System messages + user-message builders
      pipeline.py         Two-phase async orchestrator + cross-topic synthesis (grounded by bridge tags) + summarize_communities() + answer_archive_question()/extract_query_tags()
      renderer.py         Jinja2 HTML rendering
      tag_normalizer.py   Tag synonym map, normalize_tags() (Pydantic validator)
      tag_graph.py        Tag co-occurrence graph + Louvain community detection + find_bridge_tags() + group_articles_by_community()
      urgency.py          Urgency alerting: absolute threshold + z-score (SQLite-backed)
      bullet_diff.py      Historical diffing vs. yesterday's bullets (SQLite-backed)
      db.py               SQLite tracking db: schema, connection helper, output_dir/db_path safety guard, run registration
      article_archive.py  Persists each run's article summaries (source material), linked to run_id
      overview_archive.py Persists each run's cross-topic synthesis overview bullets, linked to run_id
      archive_query.py    Graph-guided retrieval: find_relevant_communities() — pure SQL, no LLM calls
      tag_tracking.py     Per-run tag-graph persistence (linked to run_id) + emerging-tag z-score + community-summary persistence (SQLite-backed)
      rdf_export.py        Builds an RDF/Turtle export of the tracking db (SKOS/PROV-O/schema.org + a custom stratrep: namespace) for `export-rdf`
      tracing.py          Langfuse / Phoenix instrumentation (opt-in)
    templates/            Jinja2 templates (base, index, topic)
    data/rss_feeds/       One JSON file per topic listing feed URLs — packaged as wheel data
    flows/daily_report.py Prefect flow (10 tasks) for scheduled runs — no `ask` equivalent, deliberately (see above)
    flows/export_rdf_flow.py Prefect flow wrapping export-rdf — not yet scheduled (see above)
    cli.py                typer CLI entrypoint — three commands: run, ask, export-rdf
    paths.py              default_data_dir() — resolves bundled rss_feeds/ via importlib.resources
    config/topic_order.py Ordered topic slugs + display titles
tests/                  Per-module test files + conftest.py fixtures
pyproject.toml          Package metadata, dependencies, extras, console script
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
  `rebuild_graph_data`, `record_emerging_tag_alerts`, `record_bridge_tags`,
  `record_articles`, `load_articles`, `record_community_summaries`) opens
  and closes its own short connection. This is deliberate: a
  `sqlite3.Connection` isn't picklable, so a shared one can't safely cross a
  Prefect task boundary.
- **Community summaries are LLM-grounded in that community's articles, not
  in the label alone.** `pipeline.summarize_communities()` calls
  `tag_graph.group_articles_by_community()` to gather the article summaries
  whose tags belong to each Louvain community, then makes one LLM call per
  community. A per-community failure is isolated (logged, omitted from the
  result) and never blocks the others — same pattern as
  `bullet_diff.diff_all_topics`. `community_summaries`/
  `community_summary_tags` store each community's own member tags rather
  than reconstructing them later, since Louvain community membership
  (`build_display_graph`) is never itself persisted.
- **`ask` (archive_query.py + pipeline.answer_archive_question) is
  graph-guided retrieval, not full GraphRAG — deliberately.** No
  embeddings, no hierarchical multi-level community summarization. It
  currently reads `community_summaries` only, not raw article text
  (`article_archive.py`'s `articles`/`article_summary_bullets` tables are
  persisted but not yet consumed by any retrieval path — available for a
  future mode grounded in raw text). Don't add embeddings or a deeper
  community hierarchy without discussing it first; this scope was a
  considered choice for a single-user tool, not a first step assumed to
  need deepening.
- **`rdf_export.py` complements `tag_graph.py`, it doesn't replace it.**
  `export-rdf` reads the tracking database (articles, tags, community
  summaries, bridge tags, per-topic strategic bullets, urgency scores,
  cross-topic overviews) and builds an RDF graph reusing standard
  vocabularies — SKOS for tags/communities, PROV-O for run lineage,
  schema.org for article metadata, a small custom `stratrep:` namespace
  for everything domain-specific. `tag_graph.py`'s Louvain computation and
  its JSON/HTML output are untouched; don't fold RDF export logic into
  that module or make one depend on the other. `--since` (a run_id or ISO
  timestamp) only filters which runs are included in a given export — it
  does not merge into an existing `.ttl` file; don't add merge/watermark
  logic without discussing it first, since this was a deliberate v1 scope
  choice, not an oversight.
- **`{topic}_summaries.html` carries `schema:Article` JSON-LD per article,
  built by `renderer._build_article_jsonld()` — kept in sync with
  `rdf_export.py`'s schema.org mapping (`headline`/`url`/`datePublished`),
  same fields, same vocabulary.** `index.html` deliberately does not get
  JSON-LD: the Strategic Overview and per-topic bullets are LLM-synthesized
  content with no schema.org type, and inventing one there would be the
  same vocabulary misuse `rdf_export.py`'s `stratrep:` namespace exists to
  avoid. The JSON-LD string is built and escaped in Python (`<`, `>`, `&` →
  `\uXXXX`) before being marked `| safe` in the template — article titles
  are RSS content we don't control, so a title containing `</script>` must
  not be able to break out of the block, same threat model as the HTML
  autoescaping above it. Don't switch this to a Jinja2 `tojson` filter
  without keeping that same escaping; a bare `jinja2.Environment` (unlike
  Flask's) doesn't provide `tojson` by default.
- **`archive_query.find_relevant_communities()` has no LLM dependency —
  keep it that way.** It's pure SQL (two-pass: exact tag membership, then
  a label/summary substring fallback). LLM orchestration (extracting query
  tags, synthesizing the final answer) belongs in `pipeline.py`, matching
  this project's existing split between "pure DB/graph access" modules
  and pipeline.py's LLM-calling orchestration.
- **Bridge tags ground cross-topic synthesis in graph structure, not
  invention.** `synthesize_cross_topic()` computes `find_bridge_tags()`
  (tags spanning 3+ topics that day — a structural signal, no LLM
  involved) and includes them in the prompt as candidate leads the model
  must confirm, not repeat verbatim. If you touch `build_cross_topic_prompt`
  or `synthesize_cross_topic`, keep this: bridge tags are grounding
  context, not a replacement for the model's own reasoning.
- **`record_bridge_tags(db_path, run_id, bridge_tags)` is self-contained**
  (stores each bridge tag's own topics in `bridge_tag_topics`, rather than
  joining against `tag_topics`), because `cli.py` and the Prefect flow call
  the emerging-tag block (which persists this) at *different* points
  relative to cross-topic synthesis in their pipeline order — don't
  "simplify" this into a join, it'll silently return incomplete data
  depending on which entry point ran it.
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
