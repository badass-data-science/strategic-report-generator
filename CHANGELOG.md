# Changelog

All notable changes to this project are documented here. Entries are
grouped by date rather than a semantic version number, since this project
doesn't tag releases.

## 2026-08-13

### Changed
- `export_rdf_flow.py` is no longer scheduled by its own cron
  (`0 4 * * *`). It now registers a Prefect Automation (a
  `DeploymentEventTrigger`, via `.serve(triggers=[...])`) that fires it
  immediately when the `daily-strategic-report` deployment's flow run
  completes, rather than waiting until a fixed 04:00 offset. The two
  processes remain fully independent — own `.serve()` call, own systemd
  unit, no in-process subflow call — only the trigger changed from
  time-based to event-based. `export_rdf_flow.py` resolves
  `daily_report_flow`'s deployment id at startup via
  `read_deployment_by_name`, so `daily_report.py`'s scheduler must already
  be registered before this one starts; systemd's existing
  `Restart=on-failure`/`RestartSec=30` handles the case where it isn't yet
  (e.g. on a cold multi-unit boot).

## 2026-08-05

### Added
- Five new topic categories — `feeds_edge_computing.json` (5 feeds),
  `feeds_energy.json` (17 feeds), `feeds_iot.json` (40 feeds),
  `feeds_usa_news.json` (96 feeds), and `feeds_world_news.json` (112
  feeds) — registered in `topic_order.py`'s `list_directories_and_titles`.
  The pipeline now covers 19 topics, up from 14.
- `validate-feeds --fix` run covering all 19 topic categories (876 feeds
  checked in total). Removed 22 dead feeds total (21 from the five new
  categories, 1 from the existing `feeds_forex`) and logged each one in
  `data/rss_feeds/REMOVED.json` under an `"exception"` field. Final counts
  after pruning: `feeds_edge_computing` 4, `feeds_energy` 16, `feeds_iot`
  37, `feeds_usa_news` 91, `feeds_world_news` 101.

### Fixed
- `feed_validation._check_one_feed` had no timeout on `feedparser.parse`,
  so a single feed with a hanging connection (no TCP RST, no server
  response) stalled its whole topic's `asyncio.gather` — and, worse, kept
  the underlying worker thread wedged permanently: `asyncio.to_thread`
  runs the blocking call on the default `ThreadPoolExecutor`, and wrapping
  the await in `asyncio.wait_for` only stops *waiting*, it doesn't cancel
  the thread itself. Enough hung feeds in one topic exhausts the pool and
  every later `to_thread()` call queues forever waiting for a free worker
  — this is what happened validating `feeds_usa_news`, live during this
  category rollout. Fixed with `socket.setdefaulttimeout(15)` before the
  fetch, which makes the underlying connection actually raise instead of
  hanging (feedparser uses `urllib`, which honors the process-global
  default timeout when no explicit one is passed). New regression test
  (`test_sets_socket_timeout_before_fetching`) asserts the timeout is set.

## 2026-08-04

### Added
- New `blog-posts/` directory: human-written companion articles about
  this project, not code and not shipped with the package. First entry,
  `strategic-intelligence-knowledge-graph.md`, covers the nightly tag
  co-occurrence graph (Louvain community detection, bridge tags),
  graph-guided archive retrieval, and the RDF export (ontology reuse via
  SKOS/PROV-O/schema.org, why RDF over a property graph, why it runs as
  its own scheduled process) — a companion to the earlier "Daily
  Strategic Intelligence, Automated" post. Includes a screenshot of the
  tag co-occurrence graph (`web-based-tag-graph.png`).
- `ruff` and `mypy` adopted for the first time in this repo, matching the
  narrow rule selection already used across the rest of this stack
  (graph-nexus, graph-nexus-ask): `select = ["E", "F", "I", "UP"]`,
  `line-length = 100`, `mypy --strict` across both `strategic_reports/` and
  `tests/`. New `lint`/`typecheck` optional-dependency groups in
  `pyproject.toml`. CI (`.github/workflows/tests.yml`) gained matching
  `lint`/`typecheck` jobs alongside the existing `pytest` job. README gained
  Ruff/mypy badges.
- Two new topic categories, `feeds_forex.json` (40 feeds) and
  `feeds_robotics.json` (41 feeds), registered in `topic_order.py`'s
  `list_directories_and_titles` — the pipeline now covers 14 topics, up
  from 12.
- Full RSS feed validation pass across all 14 topic categories (718 feeds
  checked in total). A feed counts as dead on a fetch exception (DNS
  failure, SSL handshake failure, timeout, connection refused), an XML
  parse error with zero salvageable entries (a feed that's merely `bozo`
  but still yielded usable entries is left alone), or an HTTP 200 response
  with zero parseable entries. Removed 112 dead feeds total (4 from the
  two new categories, 108 from the 12 existing ones) and logged each one
  in `data/rss_feeds/REMOVED.json` under an `"exception"` field, alongside
  the small number of feeds already excluded there by hand under a
  `"reason"` field (e.g. "I do not want personal websites.") — the new
  entries are appended, not merged into or replacing the existing ones.
  `REMOVED.json` also reformatted to consistent 4-space JSON indentation;
  it's a human-readable audit log, not read by any code path.
- New `strategic-reports validate-feeds [--fix]` CLI command
  (`core/feed_validation.py`), so the validation pass above can be re-run
  in the future instead of redone ad hoc. Checks every feed in a topic
  concurrently, reusing `ingestion.py`'s
  `asyncio.to_thread(feedparser.parse, ...)` + `asyncio.gather` pattern.
  Without `--fix`, only reports failures; with it, prunes them out of
  their `feeds_*.json` and appends them to `REMOVED.json` — safe to
  re-run periodically, since passing feeds are left untouched and repeated
  runs accumulate removal history rather than overwriting it. Deliberately
  not wired into `run` or the Prefect flow: feed health doesn't change day
  to day the way news does, so this is a maintenance utility invoked on
  demand, the same kind of exception to the run/flow parity convention as
  `ask` (see `AGENTS.md`). 13 new tests in `tests/test_feed_validation.py`.
  217 tests total, up from 204.

### Changed
- Renamed the `export-rdf` Prefect flow (and its deployment) to
  `daily-strategic-report-export-rdf`, so it's distinguishable in the
  Prefect UI from other codebases' RDF-export flows now and in the future.
  The CLI command name (`export-rdf`) is unaffected — only the Prefect
  flow/deployment identifier changed. `prefect deployment run` invocations
  now use `'daily-strategic-report-export-rdf/daily-strategic-report-export-rdf'`.

### Fixed
- `pipeline.py`'s community-summary gathering, `bullet_diff.py`'s
  bullet-diff gathering, and `pipeline.py`'s topic-ingestion gathering
  (`run_pipeline`/`_process_topic`) each only checked
  `isinstance(item, Exception)` on `asyncio.gather(..., return_exceptions=
  True)` results. `asyncio.CancelledError` is a `BaseException` subclass,
  not an `Exception` subclass — a cancelled task would slip past the guard
  and crash trying to unpack it as a normal result instead of being caught
  and logged like any other per-task failure. Found via `mypy --strict`,
  not a live production report. Also renamed a `cli.py` loop variable
  (`alert` used for both `EmergingTagAlert` and unrelated `UrgencyAlert`
  results) that was masking a real type-safety gap, coincidentally
  harmless only because both classes happen to implement `.summary()`.
- `strategic_reports/daily/core/__init__.py`: seven re-exported names
  (`append_bullet_run`, `diff_all_topics`, `load_bullet_history`,
  `UrgencyAlert`, `append_run`, `check_alerts`, `load_history`) were
  imported for re-export but missing from `__all__` — caught as both a
  ruff `F401` (unused import) and a mypy `attr-defined` error against
  `cli.py`, which does use them. Added to `__all__`.

## 2026-08-03

### Added
- `export-rdf` is now scheduled: `flows/export_rdf_flow.py` runs daily at
  **04:00 America/Los_Angeles** via its own `.serve()` call — a separate
  process from `daily_report_flow` (own systemd unit), not a task folded
  into it, keeping the same independence from the daily pipeline that the
  CLI command already has. Always a full rebuild (no `--since` watermark
  tracking on the scheduled run). The deployment's `db_path` reads from
  the same tracking database `daily_report_flow`'s deployment writes to
  (`$HOME/output/daily-strategic-report-from-RSS-feeds/strategic-reports.db`),
  and writes to `knowledge_graph.ttl` alongside it — both anchored via
  `Path.home()`, not cwd.

### Removed
- `export_rdf_flow.py`'s typer/CLI-args entry point (`--db-path`/
  `--output`/`--since` invoked directly via `python -m
  ...flows.export_rdf_flow`) — superseded by `prefect deployment run
  'export-rdf/export-rdf' --param since=...` now that a deployment
  exists. `cli.py export-rdf` remains available for genuinely
  Prefect-independent ad-hoc use.

## 2026-08-02

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
- New `export-rdf` CLI command: `python -m strategic_reports.daily.cli
  export-rdf --db-path ... --output knowledge_graph.ttl [--since
  <run_id-or-timestamp>]` exports the accumulated archive as an RDF
  (Turtle) knowledge graph. Complements `tag_graph.py`'s per-run
  co-occurrence JSON/HTML output — doesn't replace or recompute it, and
  the two are otherwise unconnected code paths. `export-rdf` is a third
  CLI-only command, alongside `ask` — a deliberate exception to the
  run/flow parity convention.
- New `rdf_export.py`: reuses standard vocabularies rather than inventing
  a bespoke schema — SKOS (tags as `skos:Concept`, Louvain communities as
  `skos:Collection`), PROV-O (every fact traces to the run that produced
  it via `prov:wasGeneratedBy`), schema.org (article bibliographic
  fields). A small custom `stratrep:` namespace covers what's genuinely
  domain-specific (topics, urgency scores, bridge-tag observations, the
  cross-topic overview). `--since` filters which runs are included in a
  given export (a `run_id` or ISO timestamp) but does not merge into an
  existing `.ttl` file — each invocation writes a fresh file; a
  deliberate v1 scope choice, not an oversight.
- New `flows/export_rdf_flow.py`: wraps `export-rdf` as a Prefect
  `@flow`/`@task`, in its own file rather than folded into
  `daily_report.py`, since `export-rdf` is deliberately independent of the
  daily pipeline (see `AGENTS.md`). **Not scheduled** — no
  `CronSchedule`/`.serve()` call yet; run it directly:
  `python -m strategic_reports.daily.flows.export_rdf_flow --db-path ...
  --output ...`, same flags as the CLI command. Scheduling cadence,
  full-vs-incremental-on-schedule, and single- vs. two-process serving
  were deliberately deferred rather than decided.
- New `cross_topic_overviews` table + `overview_archive.py`
  (`record_overview()`): the cross-topic synthesis overview was previously
  rendered into `index.html` but never persisted anywhere. Now persisted
  in both `cli.py run` and the Prefect flow's `run-cross-topic-synthesis`
  task (folded in rather than added as a separate task, since it's a
  one-line persist tied directly to that task's own output), following
  the same "never blocks rendering on failure" pattern as the other
  optional persistence steps.
- `{topic}_summaries.html` pages now carry `schema:Article` JSON-LD markup
  per article (`headline`/`url`/`datePublished`), matching the same fields
  `rdf_export.py` maps onto `schema:Article` — for structured-data
  consumers (search engines, scrapers) that only have access to the HTML,
  not `--db-path`. `index.html` is deliberately left unmarked: the
  Strategic Overview and per-topic bullets have no schema.org type, and
  inventing one would misuse the vocabulary the same way `rdf_export.py`'s
  `stratrep:` namespace exists to avoid. The JSON-LD payload is built and
  escaped in Python (`renderer._build_article_jsonld()`) before being
  embedded — RSS-sourced article titles are escaped (`<`, `>`, `&` →
  `\uXXXX`) so a title containing `</script>` can't break out of the
  block, the same threat model as the existing HTML autoescaping.
- Added `rdflib>=7.0.0` as a core dependency.
- **Breaking CLI invocation change**: `cli.py` now has three commands
  (`run`, `ask`, `export-rdf`), so naming one explicitly is required going
  forward (`... cli.py run --output-dir ...`) — typer's single-command
  auto-invoke shorthand (bare `... cli.py --output-dir ...`) no longer
  applies once there's more than one command.
- New tests across `tests/test_archive_query.py` (`find_relevant_communities()`
  exact/substring matching, dedup, ordering, limit), `tests/test_pipeline.py`
  (`extract_query_tags()`/`answer_archive_question()`),
  `tests/test_overview_archive.py` and `tests/test_rdf_export.py` (overview
  bullet round-trip/ordering, ontology mapping for
  articles/tags/communities/bridge tags/urgency/bullets/overview, `--since`
  filtering, Turtle serialization round-trip), and `tests/test_renderer.py`
  (JSON-LD field correctness, `</script>` breakout case). 204 tests total,
  up from 177. Verified end-to-end with integration smoke tests (the real
  `ask` command failing cleanly against an unreachable model and the full
  success path against a seeded archive; `export-rdf` and its Prefect flow
  run against a real seeded database, producing valid, parseable Turtle).

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
- The scheduled deployment's fixed `output_dir`/`db_path` (set once in
  `daily_report_flow.serve()`'s `parameters={}`, since a cron-triggered
  run has no CLI invocation to supply them) now point at
  `$HOME/output/daily-strategic-report-from-RSS-feeds/{daily-report,
  strategic-reports.db}`, anchored via `Path.home()` rather than the
  process's working directory. Removed the now-unused `_DEFAULT_HOME`
  module variable this replaced (it had no other callers).

### Removed
- **The remote-upload step** (`upload-to-web-server` task) from
  `daily_report_flow` — no more SCP/SSH to badassdatascience.com. Removed
  `upload_enabled`/`ssh_key_path`/`remote_host`/`remote_user`/
  `remote_staging_dir`/`remote_web_dir` flow parameters and the `subprocess`
  import. The flow now has 10 tasks (was 11) and is at **full** feature
  parity with `cli.py run` — previously the flow's one documented
  difference from the CLI was this upload step; that asymmetry no longer
  exists.

### Fixed
- `daily_report_flow` now validates `instructor_mode` up front (raises
  `ValueError` before any task runs) instead of silently falling back to
  `TOOLS` on an invalid value — matches `cli.py run`'s existing
  fail-loudly behavior for the same input. A full functionality audit
  against `cli.py run` confirmed this was the only real behavioral gap;
  every persistence/business-logic step (including `record_overview`) was
  already present in both entry points.

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
