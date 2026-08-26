# AGENTS.md

Guidance for AI coding agents working in this repository. See `README.md` for
the full pipeline design and user-facing docs — this file is the quick
orientation for making changes.

## What this is

A daily briefing pipeline: fetches RSS across 17 active topics (19 feed
configs exist under `data/rss_feeds/`, but `feeds_usa_news.json` and
`feeds_world_news.json` are deliberately excluded from
`topic_order.py`'s `list_directories_and_titles` for now — kept on disk
for later reuse, not deleted), summarizes and
synthesizes strategic recommendations via an LLM (provider-agnostic via
litellm), renders an HTML report + tag co-occurrence graph, and tracks
urgency scores/strategic bullets/article summaries/community summaries
across runs in a PostgreSQL database. Two scheduled/batch entry points, kept
at full feature parity with each other:

- `python -m strategic_reports.daily.cli run` — the batch pipeline, run once, manually
- `strategic_reports/daily/flows/daily_report.py` — the same pipeline as a
  Prefect flow, scheduled daily via cron

If you add a pipeline step to one entry point (cross-topic synthesis,
urgency alerts, bullet diffing, tag graph, community summaries, systems
signals), add it to the other too unless told otherwise — this parity was
an explicit, deliberate decision. Systems signals is the running
cautionary example of what happens when this rule is skipped: it was
wired into `cli.py`/`flows/daily_report.py` first, and a separate,
hand-maintained Airflow port of this same flow (outside this repo, not
tracked here) silently kept rendering a stale "no signals" state until
the drift was noticed and fixed as a follow-up. If you're aware of other
mirrors of this pipeline living outside this repo, treat their drift the
same way — worth a mention to the user, not silently left alone.

A second command, `python -m strategic_reports.daily.cli ask "<question>"`,
is a deliberate exception: an interactive, human-in-the-loop archive
query (graph-guided retrieval over `community_summaries` — see
`archive_query.py`), not a scheduled batch step. It has no Prefect
equivalent, and that's intentional, not a parity gap to fix.

A third command, `python -m strategic_reports.daily.cli export-rdf`, is
the same kind of exception **on the CLI side**: an on-demand export of the
tracking database to RDF/Turtle (see `rdf_export.py`), not part of
`run`'s pipeline. It complements `tag_graph.py`'s per-run co-occurrence
JSON/HTML — it does not replace or recompute it, and touching one should
not require touching the other.

On the Prefect side, `export-rdf` is **not** a parity exception: it runs
as `daily_report_flow`'s last task (`export_rdf_task`, in
`flows/daily_report.py`), so it fires automatically at the end of every
scheduled run — no separate flow file, deployment, or process. This used
to be its own flow (`flows/export_rdf_flow.py`, later triggered off
`daily_report_flow`'s completion via a Prefect Automation) kept
deliberately independent so a crash/restart of one wouldn't touch the
other; that independence was traded away on purpose for operational
simplicity — one flow/deployment to run and monitor instead of two — so
don't reintroduce a separate export-rdf flow file without being asked.
The task fails gracefully (logs a warning, doesn't raise) since the HTML
report has already rendered by the time it runs, matching the pattern used
by this flow's other archival/side-effect tasks (`archive-articles`,
`check-urgency-alerts`, etc.). Always a full rebuild — no `--since`
watermark tracking, matching the CLI command's scope — and writes
`knowledge_graph.ttl` next to `output_dir` (`output_dir.parent`, not
`--output-dir` itself, which is wiped and recreated every run — see
`export_rdf_task` in `flows/daily_report.py`). There's no local `db_path`
directory to piggyback on anymore now that `--database-url` is a
connection string, not a file path. `cli.py export-rdf` remains available,
unchanged, for ad-hoc runs with a different `--since` and an explicit
`--output` path.

A fourth command, `python -m strategic_reports.daily.cli validate-feeds
[--fix]`, is the same kind of exception: a maintenance utility over the
`feeds_*.json` configs (see `feed_validation.py`), not a pipeline step.
Feed health doesn't change day to day the way news does, so it isn't run
as part of `run` or the Prefect flow — it's meant to be invoked on demand,
occasionally, not on every scheduled run. `--fix` prunes failing feeds and
logs them in `data/rss_feeds/REMOVED.json`, using the same
`{"title", "url", "exception"}` shape as the handful of feeds already
removed there by hand (those use `"reason"` instead) — appends to that
file rather than overwriting it, so re-running `--fix` periodically
accumulates history. It has no Prefect equivalent, and that's intentional,
same as `ask`.

A fifth command, `python -m strategic_reports.daily.cli db status`, is yet
another on-demand exception, but reports on the *pipeline* rather than the
*news*: run cadence (gaps between `runs` rows, staleness past
`--stale-after-hours`) and, for the most recent `--recent-runs` runs,
whether any derived table (`articles`, `tag_counts`, `urgency_scores`,
`bullets`, `community_summaries`, `cross_topic_overviews`) is empty
despite a nonzero `article_count` — the read-side check for the exact
silent-partial-persistence bug `insert_rows_isolating_failures` (`db.py`)
guards against at write time. See `db_status.py` (query/flag logic,
read-only, no new persistence) and `renderer.render_db_status()` (the
`--html` output). Plain text on stdout by default; `--json` swaps that for
the same report as JSON (for monitoring/alerting, not for humans) and
suppresses the plain text; `--html <path>` additionally writes a
standalone page and, if combined with `--json`, prints its confirmation to
stderr instead of stdout so a monitoring parser's stdin stays clean JSON.
Like `validate-feeds`, it has no Prefect equivalent — it's a diagnostic you
run, not a pipeline step.

**CLI invocation shape**: `cli.py` now has six commands (`run`, `ask`,
`export-rdf`, `validate-feeds`, `db upgrade`, `db status`), so naming one
explicitly is required (`... cli.py run --output-dir ...`) — typer's
single-command auto-invoke shorthand (bare `... cli.py --output-dir ...`)
only applies when there's exactly one command, and no longer applies here.
If you ever reduce back to one command, that shorthand returns; don't
assume it's available with two or more.

## Setup

```bash
pip install -e ".[flow,test,lint,typecheck]"
```

Package metadata, dependencies, and extras (`flow` for Prefect, `test` for
pytest, `lint` for ruff, `typecheck` for mypy) all live in `pyproject.toml`
— there is no `requirements.txt`.
`data/rss_feeds/*.json` and `flows/` ship as package data inside
`strategic_reports/daily/` (see `paths.py`'s `default_data_dir()`), so the
package is self-contained even though it isn't published to PyPI.

No API keys are required to run the test suite — the LLM client is fully
mocked in tests. Running the actual pipeline requires `LLM_MODEL` and the
matching provider credentials (see README's Quick Start).

## Tests

```bash
pytest
ruff check .
mypy
```

- 293 tests across `tests/test_*.py`, no real network or LLM calls. Unlike
  before the PostgreSQL migration, DB-touching tests now need a reachable
  Postgres instance (`DATABASE_URL` env var) — see `docker-compose.yml` for
  local dev; CI provides one as a service container. CI
  (`.github/workflows/tests.yml`) runs `pytest`/`lint`/`typecheck` as
  separate jobs on every push/PR to `main`; only the `pytest` job has the
  Postgres service (`lint`/`typecheck` are static, no DB needed).
- `pytest.ini` sets `asyncio_mode = auto` — async test functions don't need
  `@pytest.mark.asyncio`.
- Run the whole suite after any change to `strategic_reports/daily/core/*`;
  it's fast enough that there's no reason to scope it down.
- `ruff` is configured with `select = ["E", "F", "I", "UP"]` and
  `line-length = 100` — deliberately the same narrow selection used across
  the rest of this stack (graph-nexus, graph-nexus-ask), not ruff's full
  default rule set. `strategic_reports/daily/core/__init__.py` has a
  per-file `E402` ignore for its imports-after-`configure_logging()`
  ordering (see that file's docstring for why).
- `mypy --strict` covers both `strategic_reports/` and `tests/`.

## Code layout

```
strategic_reports/
  py.typed                 PEP 561 marker
  daily/
    core/
      models.py          Pydantic models: RawArticle → ArticleSummary → TopicResult → CrossTopicSynthesis → BulletDiff
      llm_client.py       Async LLMClient: litellm + instructor + tenacity retry
      ingestion.py        Async RSS fetching
      feed_validation.py  Async RSS feed health checks + REMOVED.json pruning/logging for `validate-feeds`
      prompts.py          System messages + user-message builders
      pipeline.py         Two-phase async orchestrator + cross-topic synthesis (grounded by bridge tags) + summarize_communities() + answer_archive_question()/extract_query_tags()
      renderer.py         Jinja2 HTML rendering; render_db_status() for `db status --html`
      tag_normalizer.py   Tag synonym map, normalize_tags() (Pydantic validator)
      tag_graph.py        Tag co-occurrence graph + Louvain community detection + find_bridge_tags() + group_articles_by_community()
      urgency.py          Urgency alerting: absolute threshold + z-score (PostgreSQL-backed)
      bullet_diff.py      Historical diffing vs. yesterday's bullets (PostgreSQL-backed)
      db.py               PostgreSQL tracking db: pooled connection helper, reachability check, run registration, shared insert_rows_isolating_failures helper (schema lives in alembic/, not here)
      db_status.py        Read-only run cadence + per-run persistence health check + db_status_as_dict() for `db status`
      article_archive.py  Persists each run's article summaries (source material), linked to run_id
      overview_archive.py Persists each run's cross-topic synthesis overview bullets, linked to run_id
      archive_query.py    Graph-guided retrieval: find_relevant_communities() — pure SQL, no LLM calls
      tag_tracking.py     Per-run tag-graph persistence (linked to run_id) + emerging-tag z-score + community-summary persistence (PostgreSQL-backed)
      systems_signals.py  Lagged/partial correlation over topic urgency + tag coverage rate history; FDR correction + five-filter tag-rate chain; read-only, nothing persisted
      rdf_export.py        Builds an RDF/Turtle export of the tracking db (SKOS/PROV-O/schema.org + a custom stratrep: namespace) for `export-rdf`
      tracing.py          Langfuse / Phoenix instrumentation (opt-in)
    templates/            Jinja2 templates (base, index, topic, db_status)
    data/rss_feeds/       One JSON file per topic listing feed URLs — packaged as wheel data
      REMOVED.json        Audit log of feeds removed by `validate-feeds --fix` or by hand
    flows/daily_report.py Prefect flow (12 tasks, incl. export-rdf as the last one) for scheduled runs — no `ask` equivalent, deliberately (see above)
    cli.py                typer CLI entrypoint — six commands: run, ask, export-rdf, validate-feeds, db upgrade, db status
    paths.py              default_data_dir() — resolves bundled rss_feeds/ via importlib.resources
    config/topic_order.py Ordered topic slugs + display titles
alembic/                Tracking-db schema migrations (Postgres) — versions/0001_initial_schema.py is the whole schema, hand-written, no autogeneration (no SQLAlchemy models in this codebase)
tests/                  Per-module test files + conftest.py fixtures (database_url fixture requires a reachable Postgres — see docker-compose.yml)
blog-posts/             Human-written companion articles about this project — not code, not shipped
pyproject.toml          Package metadata, dependencies, extras, console script
docker-compose.yml      Local-dev Postgres for the database_url test fixture / manual runs
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
  new options rather than inventing a new config mechanism. Exception:
  `--output-dir` is required (no default, no env var) on both entry points —
  deliberate, not an oversight. `--database-url` is required too but does
  have an env var (`DATABASE_URL`, also loadable from a gitignored `.env`
  via `load_dotenv()` at the top of `cli.py`/`flows/daily_report.py`) —
  it's a secret, not a path-collision risk, so the old `--db-path`
  no-default-no-envvar rationale doesn't carry over; see below.
- **`--output-dir` is deleted and recreated on every run** (see
  `render_report()`). Never assume anything written there survives past the
  current run, and never point persistent state at a path inside it.
- **Schema lives in `alembic/`, not in `db.py`.** Run `alembic upgrade head`
  (or `strategic-reports db upgrade`) once per environment, explicitly — it
  is not applied implicitly on connect the way the old SQLite `_SCHEMA`
  script was. `db.ensure_database_reachable()` is the fail-fast check that
  replaced the old SQLite-file-collision guard (`ensure_safe_db_path()`,
  now removed) — call it before any real work if you add a third entry
  point that touches the tracking db.
- **Tracking-db functions take `database_url: str`, not a live
  `psycopg.Connection`.** Each call (`load_history`, `append_run`,
  `load_bullet_history`, `append_bullet_run`, `record_run`, `record_tags`,
  `load_tag_rate_history`, `rebuild_graph_data`, `record_emerging_tag_alerts`,
  `record_bridge_tags`, `record_articles`, `load_articles`,
  `record_community_summaries`) opens its own connection via
  `db.get_connection()`, backed by a process-local connection pool keyed by
  `database_url` (see `db._get_pool()`). This is deliberate: a live
  connection isn't picklable, so it can't safely cross a Prefect task
  boundary — only the connection-string type changed (`Path` → `str`), not
  this calling convention.
- **SQL placeholders are `%s` (psycopg), not `?`.** And `conn.executemany()`
  doesn't exist on a psycopg `Connection` the way it did on
  `sqlite3.Connection` — use `conn.cursor().executemany(...)`. Both are easy
  to get wrong if copying an old SQLite-era pattern.
- **Bulk inserts must isolate per-row failures — one bad row must never
  silently discard the rest of that run's data.** `get_connection()`'s
  pooled connection rolls back its *entire* transaction when any exception
  escapes the `with` block, so a single bad article/tag/community-summary
  row used to take out every other row already inserted that run, with no
  trace beyond a one-line stderr/log warning at the call site — this
  actually happened in production (two real runs ended up with fully
  populated `tag_counts` but zero rows in `articles`/`article_tags`, found
  by manually cross-referencing systems-signals output against real
  articles). `article_archive.record_articles` wraps each article's insert
  in its own `conn.transaction()` savepoint. Every other bulk insert
  against the tracking db (`tag_tracking.record_tags`/
  `record_community_summaries`, `urgency.append_run`,
  `bullet_diff.append_bullet_run`) goes through a shared helper,
  `db.insert_rows_isolating_failures` (originally private to
  `tag_tracking.py`, promoted to `db.py` once a third and fourth caller
  needed it), which batches first (a single `executemany()` inside one
  savepoint) and only falls back to a per-row savepoint loop if the batch
  as a whole fails — a table like `tag_edges` can have tens of thousands
  of rows in one run, so an unconditional per-row loop the way
  `record_articles` uses would be a real performance regression for the
  common case where nothing fails. All five functions now return a
  failed-row count instead of `None`; `cli.py` and `flows/daily_report.py`
  log a partial-failure warning at each call site when it's nonzero. If
  you add another bulk insert against the tracking db, use
  `db.insert_rows_isolating_failures` rather than a bare `executemany()`
  in one shared transaction.
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
- **`systems_signals.py` is read-only and persists nothing — don't add a
  table for it without discussing first.** `topic_urgency_lagged_correlations()`
  and `tag_rate_lagged_correlations()` recompute their result fresh from
  `urgency_scores`/`tag_counts`/`tag_edges`/`tag_topics`/
  `community_summary_tags`/`articles` on every call; the only place the
  output goes is that run's `index.html` Systems Signals section, and it
  doesn't survive to the next run — this was a deliberate choice, not an
  oversight, made explicitly when asked. An uncorrected all-pairs scan is
  almost pure noise (a 14-run archive produced ~40 "significant" topic
  pairs and ~9,700 tag pairs before any correction), so both functions run
  a Benjamini-Hochberg FDR correction across every test in a single scan.
  `topic_urgency_lagged_correlations` also has two safeguards of its own
  now, added after its one live result (Energy correlated with Forex,
  r≈0.92) turned out to be a false positive on inspection: (1)
  `load_topic_urgency_series` treats a run entirely missing from
  `articles` as a gap for every topic (`_runs_missing_from`, same check
  `load_tag_rate_series` uses) instead of trusting a real-but-tainted
  urgency score computed from a run whose article archive silently
  failed to persist; (2) it's now a *partial* correlation
  (`lagged_partial_pearson`), controlling each topic out by
  `_topic_urgency_control_series` — its own leave-one-out mean urgency
  across every other tracked topic that run — since even past fix (1),
  Energy and Forex both independently correlated with that shared-trend
  control almost as strongly as with each other. Neither safeguard existed
  when Energy/Forex first shipped as a result; don't assume a future
  topic-urgency candidate has been checked for anything beyond these two
  before trusting it further.

  `tag_rate_lagged_correlations` additionally runs five structural/
  statistical filters before a candidate is even correlated or reported: a
  sparsity floor (`_MIN_ACTIVE_RUNS_FOR_TAG_CORRELATION`), a containment-
  ratio check for near-synonymous tags (`_drop_near_synonymous_pairs`), a
  Louvain-community filter reusing `community_summary_tags`
  (`_drop_topically_clustered_pairs` — the clustering itself comes from
  `tag_graph.py`, no LLM call), a partial-correlation topic-volume control
  for cross-topic confounds (`lagged_partial_pearson` +
  `_topic_weights`/`_weighted_topic_volume` — a *weighted blend* across
  every topic a tag has ever appeared under, not just its single most
  common one, since a single-topic version of this control was tried
  first and missed a confound spanning several topics at once), and a
  source-dominance filter for pairs that mostly co-occur via one recurring
  publisher (`_drop_single_source_pairs`). `lag` is counted in runs, not
  calendar days. Wired into both `cli.py run()` and the Prefect flow as
  the last step before rendering (needs the current run's own DB rows
  already persisted), following the same fail-soft
  try/except-log-warn-continue pattern as every other optional stage in
  this file — there's no CLI toggle flag, matching the existing
  convention that no optional stage here has one (see `validate-feeds`'s
  `--fix` below for the one exception, which is an action flag on a
  separate command, not a stage toggle).
- **`validate-feeds`/`feed_validation.py` is a maintenance utility over the
  feed configs, not a pipeline step — keep it that way.** It has no
  Prefect equivalent and isn't called from `run`, same deliberate
  exception as `ask` (see above). A feed counts as dead on a fetch
  exception, an XML parse error with zero salvageable entries, or an HTTP
  200 with zero entries — a feed that's merely `bozo` (minor XML quirks)
  but still yielded usable entries is left alone; don't tighten that to
  "any parse warning is dead." `remove_dead_feeds()` appends to
  `REMOVED.json` (`{"title", "url", "exception"}`, matching the
  `{"title", "url", "reason"}` shape already used for manually-curated
  exclusions) rather than overwriting it, since this is meant to be
  re-run periodically and accumulate history — don't change it to
  replace the file's contents.
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
- **`record_bridge_tags(database_url, run_id, bridge_tags)` is self-contained**
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
- `db.record_run(database_url, run_id, article_count)` must be called once per
  run, before `append_run`/`append_bullet_run` (those insert rows that
  reference `run_id` as a foreign key). `article_count` is the total
  articles considered that run — the denominator for comparing tag/urgency
  weights across runs of different sizes.
- **`blog-posts/` is the user's own editorial voice, not generated
  documentation.** Unlike `AGENTS.md`/`README.md`/`CHANGELOG.md`, don't
  edit or add to it proactively when updating docs after a code change —
  only touch it when explicitly asked to draft or revise a post.

## Git identity

This repo's local commits and pushes use the `badass-data-science` git
identity (see `git config --local user.name`), pushed via the SSH host alias
`github-badass`. Don't change this without the user's explicit say-so.
