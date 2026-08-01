# Changelog

All notable changes to this project are documented here. Entries are
grouped by date rather than a semantic version number, since this project
doesn't tag releases.

## Unreleased (`prefect-pipeline-updates-0001` branch)

### Added
- New `flows/export_rdf_flow.py`: wraps `export-rdf` as a Prefect
  `@flow`/`@task`, in its own file rather than folded into
  `daily_report.py`, since `export-rdf` is deliberately independent of the
  daily pipeline (see `AGENTS.md`). **Not scheduled** — no
  `CronSchedule`/`.serve()` call yet; run it directly:
  `python -m strategic_reports.daily.flows.export_rdf_flow --db-path ...
  --output ...`, same flags as the CLI command. Scheduling cadence,
  full-vs-incremental-on-schedule, and single- vs. two-process serving
  were deliberately deferred rather than decided.

### Removed
- **The remote-upload step** (`upload-to-web-server` task) from
  `daily_report_flow` — no more SCP/SSH to badassdatascience.com. Removed
  `upload_enabled`/`ssh_key_path`/`remote_host`/`remote_user`/
  `remote_staging_dir`/`remote_web_dir` flow parameters and the `subprocess`
  import. The flow now has 10 tasks (was 11) and is at **full** feature
  parity with `cli.py run` — previously the flow's one documented
  difference from the CLI was this upload step; that asymmetry no longer
  exists.

## Unreleased (`schema-org-html-markup` branch)

### Added
- `{topic}_summaries.html` pages now carry `schema:Article` JSON-LD markup
  per article (`headline`/`url`/`datePublished`), matching the same fields
  `rdf_export.py` already maps onto `schema:Article` — for structured-data
  consumers (search engines, scrapers) that only have access to the HTML,
  not `--db-path`. `index.html` is deliberately left unmarked: the
  Strategic Overview and per-topic bullets have no schema.org type, and
  inventing one would misuse the vocabulary the same way `rdf_export.py`'s
  `stratrep:` namespace exists to avoid.
- The JSON-LD payload is built and escaped in Python
  (`renderer._build_article_jsonld()`) before being embedded — RSS-sourced
  article titles are escaped (`<`, `>`, `&` → `\uXXXX`) so a title
  containing `</script>` can't break out of the block, the same threat
  model as the existing HTML autoescaping.
- 2 new tests (`tests/test_renderer.py`): JSON-LD field correctness, and
  the `</script>` breakout case. 204 tests total, up from 202.

## Unreleased (`rdf-export` branch)

### Added
- New `export-rdf` CLI command: `python -m strategic_reports.daily.cli
  export-rdf --db-path ... --output knowledge_graph.ttl [--since
  <run_id-or-timestamp>]` exports the accumulated archive as an RDF
  (Turtle) knowledge graph. Complements `tag_graph.py`'s per-run
  co-occurrence JSON/HTML output — doesn't replace or recompute it, and
  the two are otherwise unconnected code paths.
- New `rdf_export.py`: reuses standard vocabularies rather than inventing
  a bespoke schema — SKOS (tags as `skos:Concept`, Louvain communities as
  `skos:Collection`), PROV-O (every fact traces to the run that produced
  it via `prov:wasGeneratedBy`), schema.org (article bibliographic
  fields). A small custom `stratrep:` namespace covers what's genuinely
  domain-specific (topics, urgency scores, bridge-tag observations, the
  cross-topic overview).
- `--since` filters which runs are included in a given export (a `run_id`
  or ISO timestamp) but does not merge into an existing `.ttl` file —
  each invocation writes a fresh file; a deliberate v1 scope choice, not
  an oversight.
- New `cross_topic_overviews` table + `overview_archive.py`
  (`record_overview()`): the cross-topic synthesis overview was previously
  rendered into `index.html` but never persisted anywhere. Now persisted
  in both `cli.py run` and the Prefect flow's `run-cross-topic-synthesis`
  task (folded in rather than added as a separate task, since it's a
  one-line persist tied directly to that task's own output), following
  the same "never blocks rendering on failure" pattern as the other
  optional persistence steps.
- Added `rdflib>=7.0.0` as a core dependency.
- `export-rdf` is a fourth CLI-only command, like `ask` — a deliberate
  exception to the run/flow parity convention; no Prefect equivalent yet.
- 13 new tests (`tests/test_overview_archive.py`,
  `tests/test_rdf_export.py`): overview bullet round-trip/ordering,
  ontology mapping for articles/tags/communities/bridge
  tags/urgency/bullets/overview, `--since` filtering, Turtle
  serialization round-trip. 202 tests total, up from 189.

## Unreleased (`pypi-friendly-refactor` branch)

### Changed
- Restructured the project as a proper (if unpublished) Python package:
  added `pyproject.toml` (setuptools backend) as the sole source of truth
  for metadata, dependencies, and extras — `prefect` moved to an optional
  `flow` extra since nothing inside `strategic_reports` imports it directly,
  and `pytest`/`pytest-asyncio` moved to a `test` extra. `requirements.txt`
  is removed; CI now installs via `pip install ".[test]"`.
- `data/rss_feeds/*.json` and `flows/daily_report.py` moved inside the
  package (`strategic_reports/daily/data/rss_feeds/`,
  `strategic_reports/daily/flows/`) as wheel-included package data, so a
  built wheel is fully self-contained.
- Added `strategic_reports/daily/paths.py` (`default_data_dir()`), which
  resolves the bundled `rss_feeds/` directory via `importlib.resources`
  regardless of install method. `cli.py` and `flows/daily_report.py` both
  use it for `--data-dir`'s default; runtime state defaults (`--output-dir`,
  `--db-path`) still anchor to `Path.cwd()`/`STRATEGIC_REPORTS_HOME`, never
  to the installed package.
- Added `strategic_reports/py.typed` (PEP 561) and `__version__` in
  `strategic_reports/__init__.py`; added a `strategic-reports` console
  script entry point (`strategic_reports.daily.cli:app`).
- Trimmed tutorial-style comments (library-mechanics explainers, restating
  what standard-library/decorator syntax does) across `cli.py`,
  `flows/daily_report.py`, `core/__init__.py`, `llm_client.py`,
  `ingestion.py`, `pipeline.py`, `renderer.py`, and `prompts.py` in favor of
  comments that explain non-obvious design rationale.
- Verified via a real `pip install`/`python -m build --wheel` round-trip
  (not just editable install) that package data resolves correctly and the
  console script runs from an installed wheel, not just the source tree.

## Unreleased (`archive-query` branch)

### Added
- New `ask` CLI command: `python -m strategic_reports.daily.cli ask
  "<question>" --db-path ...` answers free-text questions about the
  accumulated archive — across every past run, not just today's — via
  graph-guided retrieval, not full GraphRAG. `pipeline.extract_query_tags()`
  pulls candidate tags from the question; `archive_query.find_relevant_communities()`
  matches them against `community_summaries` (exact tag membership first,
  substring fallback second, across all runs); `pipeline.answer_archive_question()`
  synthesizes an answer grounded strictly in what's retrieved. No
  embeddings, no hierarchical multi-level community summarization —
  deliberately out of scope, a considered choice for this project's
  single-user use case.
- New `QueryTags`/`ArchiveAnswer` Pydantic models, `SYSTEM_QUERY_TAGS`/
  `SYSTEM_ARCHIVE_ANSWER` system messages, `build_query_tags_prompt()`/
  `build_archive_answer_prompt()`.
- `ask` is the one deliberate exception to the "add every pipeline step to
  both entry points" convention: no Prefect equivalent, since it's an
  interactive human-in-the-loop command, not a scheduled batch step.
- **Breaking CLI invocation change**: `cli.py` now has two commands (`run`,
  `ask`), so naming one explicitly is required going forward
  (`... cli.py run --output-dir ...`) — typer's single-command auto-invoke
  shorthand (bare `... cli.py --output-dir ...`) no longer applies once
  there's more than one command.
- 20 new tests (`tests/test_archive_query.py`: `find_relevant_communities()`
  exact/substring matching, dedup, ordering, limit; `tests/test_pipeline.py`:
  `extract_query_tags()`/`answer_archive_question()`). 189 tests total, up
  from 177. Verified end-to-end with two integration smoke tests (the real
  `ask` command failing cleanly against an unreachable model, and the full
  success path with a mocked LLM against a seeded archive).

## 2026-08-01

### Added
- LLM-written summary per Louvain tag-community, replacing "labeled by top
  tag" with an actual paragraph: `pipeline.summarize_communities()` makes
  one LLM call per community, grounded in the article summaries whose tags
  belong to that community (`tag_graph.group_articles_by_community()`). A
  per-community failure is isolated and never blocks the others or the
  rest of the report.
- New `community_summaries`/`community_summary_tags` tables (`label`,
  `summary`, `article_count`, and each community's member tags, linked to
  `run_id`). Self-contained — stores its own member tags rather than
  reconstructing them later, since Louvain community membership
  (`build_display_graph`) is never itself persisted.
- New `CommunitySummary` Pydantic model, `SYSTEM_COMMUNITY_SUMMARY` /
  `build_community_summary_prompt()`.
- New Prefect task `summarize-communities` (flow is now 11 tasks); wired
  into `cli.py` right after the emerging-tag check too, keeping both entry
  points at parity.
- Explicitly part of the same forward-looking foundation as
  `article_archive.py` for a future interactive archive-query feature —
  still graph-guided-retrieval-inspired, not full GraphRAG (deliberately
  out of scope for now; this single-level community summary is a scoped-
  down choice, not assumed to need hierarchical deepening).
- 24 new tests (`tests/test_tag_graph.py`: `group_articles_by_community()`;
  `tests/test_pipeline.py`: `summarize_communities()` concurrency/failure
  isolation/prompt capping; `tests/test_tag_tracking.py`:
  `record_community_summaries()`). 177 tests total, up from 161.

## 2026-07-31

### Added
- Persists each run's article summaries (title, link, publish_date, summary
  bullets, tags) into the tracking database, linked to `run_id`
  (`strategic_reports/daily/core/article_archive.py`, new `articles` /
  `article_summary_bullets` / `article_tags` tables). This is the source
  material every derived signal (tags, bullets, urgency scores) is
  computed from; previously it only existed in memory during a run and was
  lost once `{topic}_summaries.html` — in the wiped `--output-dir` — was
  gone. Explicitly the foundation for a future interactive archive-query
  feature (graph-guided-retrieval-inspired, not full GraphRAG — that
  remains deliberately out of scope for now).
- New Prefect task `archive-articles` (flow is now 10 tasks); wired into
  `cli.py` right after `record_run()` too, keeping both entry points at
  parity.
- `tests/test_article_archive.py` (9 tests: round-trip, bullet/tag
  ordering, multi-topic, error/empty topics contribute nothing, run
  isolation). 161 tests total, up from 152.
- Cross-topic synthesis is now grounded by "bridge tags"
  (`tag_graph.find_bridge_tags()`): tags whose articles span 3+ topics that
  day — a structural signal from the same co-occurrence graph that powers
  `tag_graph.html`, computed independently of the LLM. Listed in the
  `synthesize_cross_topic()` prompt as candidate leads; the system message
  instructs the model to confirm each one represents a real connection
  rather than repeating it verbatim.
- `tests/test_tag_graph.py` (7 tests for `find_bridge_tags()`) and a new
  `TestBuildCrossTopicPrompt` class in `tests/test_prompts.py` (8 tests).
- `bridge_tags`/`bridge_tag_topics` tables: an audit trail of the bridge
  tags actually surfaced to the cross-topic synthesis prompt each run
  (`tag`, `count`, `rank`, topic list, linked to `run_id`) — answers "which
  tags did we point the synthesis at on day N" directly. Self-contained
  (doesn't join against `tag_topics`) since `cli.py` and the Prefect flow
  persist this at different points relative to cross-topic synthesis in
  their pipeline order.
- 5 new tests in `tests/test_tag_tracking.py` for `record_bridge_tags`.
  152 tests total, up from 132.
- Per-run tag tracking (`strategic_reports/daily/core/tag_tracking.py`):
  `tag_counts`, `tag_topics`, `tag_edges` tables store a run's tag graph
  (per-tag counts, per-tag topic membership, tag-pair co-occurrence
  weights), linked to `run_id`. `rebuild_graph_data(db_path, run_id)`
  reconstructs `tag_graph.json`'s `{"nodes", "links"}` shape from the
  database for any past run.
- Emerging-tag z-score alerts: a tag's rate this run (`count /
  article_count`) is compared against its own historical rate once it has
  7+ prior runs. Deliberately no absolute-threshold fallback for
  thin-history tags (including brand-new tags) — unlike urgency scores,
  tag rates have no meaningful absolute cutoff, so those are skipped
  rather than guessed at.
- `emerging_tag_alerts` table: an audit trail of the alerts that actually
  fired (`tag`, `count`, `rate`, `mean`, `std`, `z_score`, `run_id`,
  `created_at`) — answers "what was tag X's z-score on day N" directly,
  without redoing the historical-window calculation. Only fired alerts are
  stored; every tag's rate/z-score stays recomputable on demand from
  `tag_counts` + `runs.article_count`.
- `--tag-z-score-threshold` CLI/flow option (default `2.0`).
- New Prefect task `check-emerging-tags` (flow is now 9 tasks).
- `tests/test_tag_tracking.py` (17 tests: round-trip reconstruction, rate
  normalization, thin-history/std-zero/brand-new-tag skips, statistical
  alert firing, audit-trail persistence). 132 tests total, up from 115.
- SQLite tracking database (`strategic_reports/daily/core/db.py`): `runs`,
  `urgency_scores`, and `bullets` tables. Every row carries its own
  timestamp in addition to `run_id`; nothing is pruned.
- `runs.article_count` — total articles considered per run, the
  denominator future cross-run tag/urgency-weight comparisons need.
- Required `--db-path` CLI/flow option pointing at the tracking database.
  Guarded (`ensure_safe_db_path`) so it can never resolve inside
  `--output-dir`, which is wiped every run.
- `python -m strategic_reports.daily.cli` brought to full feature parity
  with the Prefect flow: cross-topic synthesis, urgency alerts, bullet
  diffing, and tag-graph generation. Previously the CLI only rendered
  `index.html` and per-topic summaries; only the Prefect flow did the rest.
- `tests/test_db.py`, `tests/test_bullet_diff.py` (no coverage existed for
  either area before).
- `AGENTS.md` — orientation doc for AI coding agents working in this repo.
- GitHub Actions CI (`.github/workflows/tests.yml`): runs `pytest` on every
  push/PR to `main`, no credentials required.
- `LICENSE` (MIT) and README badges (Tests / Python / License).

### Changed
- `urgency.py`/`bullet_diff.py` rewritten to read/write the SQLite tracking
  database instead of `urgency_history.json`/`bullet_history.json`. Call
  shapes are unchanged (`load_history`, `append_run`, `load_bullet_history`,
  `append_bullet_run`), just `history_path` → `db_path`; each call opens and
  closes its own short connection rather than sharing one, since a
  `sqlite3.Connection` isn't picklable and can't cross a Prefect task
  boundary.
- `--output-dir` is now required (previously defaulted to
  `output/daily/strategic-report`) and is deleted and recreated on every
  run (previously only created if missing — stale files from prior runs
  could linger).
- `STRATEGIC_REPORTS_OUTPUT_DIR` environment variable removed; `--output-dir`
  is CLI-flag-only.
- README test count/file-list corrected (92 → 115 tests, 6 → 9 files).

## 2026-07-05

### Changed
- Tags normalized to American spelling.
- Scheduled Prefect flow defaults to `instructor.Mode.TOOLS`.
- Default/example `LLM_MODEL` updated to `glm-5.2:cloud`.

## 2026-07-01

### Added
- Historical bullet diffing: LLM-classified new / continued / dropped
  strategic bullets per topic, shown as "Since yesterday" annotations.

### Fixed
- `render_html_report` task was missing the `overview` parameter.
- `urgency_score` calibration — reduced LLM anchoring at 0.85.
- Tag graph HTML: pruned via NetworkX + Louvain community detection;
  graph data inlined so the page works from a `file://` URL without a
  web server.
- Upload step now copies JSON files alongside HTML to the web root.

## 2026-06-30

### Added
- Tag normalization: synonym map + Pydantic validator.
- Tag co-occurrence network graph (D3.js force-directed).
- Cross-topic strategic synthesis (Strategic Overview section).
- Urgency alerting: absolute threshold + z-score statistical baseline.
- Web server upload step (SCP + SSH) in the Prefect flow.

### Fixed
- `AttributeError` when an RSS entry has no `link` field.

## 2026-06-29

### Added
- `--instructor-mode` flag, for models without tool-calling support.
- Prefect flow for scheduled daily execution (00:30 Pacific).
- Ollama `api_base`/`api_key` support.

## 2026-06-27

### Changed
- Refactored the strategic-reports pipeline with modern AI engineering
  patterns: instructor + Pydantic structured outputs, litellm provider
  abstraction, two-phase async architecture, per-topic error isolation.

### Removed
- Legacy pipeline code (the original `*_inator.py` scripts, notebooks, old
  prompts).

## 2026-06-07 – 2026-06-19

Initial import and early iteration, prior to the June 27 refactor.
