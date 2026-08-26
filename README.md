# Strategic Reports Pipeline

[![Tests](https://github.com/badass-data-science/strategic-report-generator/actions/workflows/tests.yml/badge.svg)](https://github.com/badass-data-science/strategic-report-generator/actions/workflows/tests.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue)](.github/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://www.mypy-lang.org/static/mypy_badge.svg)](https://mypy-lang.org/)

A daily briefing pipeline that reads recent news across 17 active topic
feeds — AI, biotech, economics, geopolitics, defense, forex, robotics,
energy, IoT, and more — and synthesizes strategic recommendations into a
linked HTML report. (Two more, USA News and World News, have feed configs
checked in but are currently excluded from the pipeline — see
`topic_order.py`.)

The pipeline doesn't just summarize each day's news — it builds a graph from
it: tags connected whenever they co-occur on the same article, clustered into
communities via Louvain community detection, each community given its own
LLM-written summary of what it's actually about. Structural signals are read
directly off that graph rather than guessed at by an LLM: which tags bridge
otherwise-unrelated domains, which tags' rates are spiking above their own
historical baseline. Every run's graph accumulates into a queryable
PostgreSQL archive spanning every past run, and `ask` lets you pose free-text questions
against that accumulated structure directly. The HTML report is this
pipeline's most visible output today; the graph and its growing archive are
the parts meant to matter more over time.

Beyond structure, the pipeline also looks for *dynamics*: lagged correlation
across each run's accumulated history surfaces candidate feedback
loops — pairs of topics or tags whose values move together now, or one run
later — filtered through several layers (false-discovery correction,
sparsity/containment/community/topic-volume/single-source checks) before
anything is reported as a real signal rather than a coincidence. See
[Systems Signals surfaces candidate feedback loops, filtered
aggressively](#systems-signals-surfaces-candidate-feedback-loops-filtered-aggressively)
below.

> **Caveat emptor.** LLM-generated strategic analysis is a starting point, not
> a substitute for human judgment. Apply your own reasoning and follow-up
> research before acting on any recommendation.

> Working on this repo with an AI coding agent? See [`AGENTS.md`](AGENTS.md)
> for setup, test commands, and code conventions. See
> [`CHANGELOG.md`](CHANGELOG.md) for what's changed recently.

---

## How it works

```
Phase 1 — RSS Ingestion  [concurrent, I/O-bound]

  feeds_ai.json ──┐
  feeds_bio.json ──┼──► asyncio.gather ──► list[RawArticle]  (per topic)
  feeds_*.json ───┘       (all topics fire simultaneously)

Phase 2 — LLM Processing  [concurrent, rate-limited by Semaphore]

  list[RawArticle]  ──► batched summarization ──► list[ArticleSummary]
  list[ArticleSummary] ──────────────────────► StrategicInsight
                                               (packaged as TopicResult)

Phase 3 — Cross-topic Synthesis  [single LLM call]

  list[TopicResult] ──► find_bridge_tags() ──► candidate cross-domain tags
  list[TopicResult] + bridge tags ──► CrossTopicSynthesis  (3–4 cross-cutting bullets)

Phase 4 — Historical Diffing  [concurrent per-topic LLM calls]

  --database-url (bullets table) ──► yesterday's bullets (per topic)
  list[TopicResult]         ──► diff vs. yesterday ──► dict[topic, BulletDiff]
                                                        (new / continued / dropped)
  (skipped on first run; today's bullets inserted into --database-url after diff)

Phase 5 — Systems Signals  [pure DB read, no LLM]

  --database-url (urgency_scores/tag_counts/tag_edges/tag_topics/
    community_summary_tags/articles) ──► topic_urgency_lagged_correlations()
                                          (FDR-corrected, archive-gap
                                          handling + leave-one-out trend
                                          control — see Design decisions)
                                      ──► tag_rate_lagged_correlations()
                                          (FDR-corrected, five-filter chain —
                                          see Design decisions)
                                      ──► list[LaggedCorrelation] x2
                                          (never persisted — recomputed fresh
                                          every run)

Phase 6 — Rendering  [Jinja2 templates]

  CrossTopicSynthesis      ──► Strategic Overview section (top of index.html)
  list[LaggedCorrelation] x2 ──► Systems Signals section (index.html)
  dict[topic, BulletDiff]  ──► "Since yesterday" annotations per topic
  list[TopicResult]   ──► index.html  (per-topic sections)
                      ──► {topic}_summaries.html  (one per topic)
                      ──► tag_graph.html + tag_graph.json  (D3.js tag network)

Phase 7 — Upload  [SCP + SSH]

  output_dir/ ──► remote staging dir ──► web root
```

The pipeline is provider-agnostic: swap the model string to run against
Ollama, Claude, OpenAI, or any other litellm-supported backend.

---

## Design decisions

### Structured outputs instead of JSON parsing

The original pipeline asked the LLM to return JSON and parsed the response
with string manipulation, catching all exceptions silently. This is replaced
by [instructor](https://github.com/jxnl/instructor) + Pydantic: the LLM
response is parsed directly into a typed model (`ArticleSummaryBatch`,
`StrategicInsight`). Field constraints — exactly 3 summary bullets, 5–20 tags,
3–5 strategy bullets — are enforced at parse time, not in the prompt. If the
model returns something invalid, instructor retries automatically with the
validation error fed back as context.

### Provider abstraction via litellm

A single model string is the only thing that changes to switch LLM providers:

```
ollama_chat/llama3.1:70b    # local or hosted Ollama
anthropic/claude-sonnet-4-6 # Claude API
gpt-4o                      # OpenAI API
```

No client code changes required.

### Two-phase async architecture

RSS ingestion and LLM inference have different bottlenecks. Feed fetching is
pure network I/O with no rate limits — all active topics' feeds fire concurrently
via `asyncio.gather`. LLM calls are also I/O-bound but API rate-limited — an
`asyncio.Semaphore` controls how many topics can hit the API simultaneously
(default: 3, tunable per provider). `feedparser` is synchronous, so it runs
in a thread pool via `asyncio.to_thread`. The old pipeline used `time.sleep(5)`
between every stage; the semaphore replaces all of that.

### Per-topic error isolation

Each topic's processing is wrapped independently. A bad RSS feed, a missing
config file, or an LLM timeout for one topic returns a `TopicResult(error=...)`
and is shown as a styled error note in the report — it does not cancel the
other 11 topics.

### Bridge tags ground cross-topic synthesis in graph structure

`synthesize_cross_topic()` doesn't rely on the LLM alone to notice
cross-domain connections. Before writing the prompt, it computes "bridge
tags" (`tag_graph.find_bridge_tags()`): tags whose articles span three or
more topics that day — a purely structural, non-LLM signal derived from
the same tag co-occurrence graph that powers `tag_graph.html`. These are
listed in the prompt as candidate leads, and the system message instructs
the model to confirm each one actually represents a substantive connection
rather than repeating it verbatim — the graph signal grounds the search,
it doesn't replace the model's judgment. Every bridge tag surfaced this way
is also persisted (`bridge_tags`/`bridge_tag_topics` — see
[Output](#output)) as an audit trail, so it's possible to later check
whether the resulting Strategic Overview actually reflected what the graph
pointed to.

### LLM-written summaries per tag community

The tag graph's Louvain communities (used for `tag_graph.html`'s color
coding) were previously labeled only by their highest-count member tag —
"policy," "biotech," and so on. `pipeline.summarize_communities()` replaces
that with an actual paragraph: one LLM call per community, grounded in the
article summaries whose tags belong to that community
(`tag_graph.group_articles_by_community()`), describing what the cluster
of coverage is substantively about — not just its label. A per-community
failure is isolated (logged, omitted) and never blocks the others or the
rest of the report. Persisted to `community_summaries`/
`community_summary_tags` (see [Output](#output)), this is the retrieval
material the `ask` command reads (see next section).

### Systems Signals surfaces candidate feedback loops, filtered aggressively

`topic_urgency_lagged_correlations()` and `tag_rate_lagged_correlations()`
(`systems_signals.py`) look for lagged correlation across each run's
accumulated history — does topic A's urgency score, or tag B's coverage
rate, predict another topic's or tag's value some number of runs later?
An uncorrected all-pairs scan is almost pure noise (on a 14-run archive it
returned roughly 40 "significant" topic pairs and just under 10,000 tag
pairs, from testing thousands of hypotheses in one pass), so every
candidate goes through a Benjamini-Hochberg false-discovery-rate
correction before it can be reported at all, on top of five
structural/statistical filters specific to the tag-rate side:

- a **sparsity floor** — a tag needs enough history in enough runs before
  it's even eligible, since two rare tags can trivially "perfectly
  correlate" just by both being nonzero in the same handful of runs;
- a **containment-ratio** check for near-synonymous tags — two labels for
  the same entity (e.g. a company and its own product line) rather than
  two independently-moving things;
- a **Louvain-community filter** for topically-adjacent-but-distinct
  pairs, reusing the exact same community detection `tag_graph.py`
  already computes rather than inventing a second clustering;
- a **partial-correlation topic-volume control** for cross-topic
  confounds — two tags riding the same broader news wave (a busy topic,
  or a trend spanning several topics at once) without one actually
  predicting the other;
- a **source-dominance filter** for pairs that mostly co-occur because one
  recurring source (e.g. a single daily digest) happens to mention both,
  not because the two things move together.

`lag` is counted in runs, not calendar days — the pipeline sometimes runs
more than once in a day. Nothing here is persisted: both functions
recompute fresh from `urgency_scores`/`tag_counts`/`tag_edges`/
`tag_topics`/`community_summary_tags`/`articles` (see [Output](#output))
on every call, and the result only ever reaches that run's `index.html`
**Systems Signals** section — not the database, not a prior report, since
`--output-dir` itself is wiped every run (see the warning in
[Output](#output)).

`topic_urgency_lagged_correlations()` has no tag-graph structure to lean
on (topics are a small, fixed vocabulary, not ~7k free-form tags), so none
of the five tag-rate filters above apply to it — but it isn't bare
either, after a real false positive found the hard way. The one candidate
this function ever reported on the live archive, Energy correlated with
Forex at lag zero (r≈0.92), turned out to be an artifact of two things:
two runs whose article archive had silently failed (`load_topic_urgency_series`
now treats a run missing entirely from `articles` as a gap for every
topic, the same `_runs_missing_from` check `load_tag_rate_series` uses,
not a false low score), and — even with those two runs excluded — both
topics independently correlating almost as strongly with the day's mean
urgency across every *other* tracked topic as they did with each other, a
topic-level version of the same "riding a shared trend" confound the
tag-rate partial-correlation control exists to catch. `topic_urgency_lagged_correlations`
now runs the same kind of partial correlation
(`_topic_urgency_control_series`: each topic's leave-one-out mean urgency
across every other topic, that run) before reporting anything. Both
fixes together dropped the Energy/Forex candidate out of contention
entirely — it no longer appears even in the raw, uncorrected candidate
list.

### `ask` is graph-guided retrieval, not full GraphRAG

`python -m strategic_reports.daily.cli ask "<question>"` (see
[Asking questions about the archive](#asking-questions-about-the-archive))
answers free-text questions over the accumulated archive — across every
past run, not just today's. The retrieval unit is the Louvain
tag-community, not raw article text or embeddings: `extract_query_tags()`
pulls candidate tags from the question, `archive_query.find_relevant_communities()`
matches them against `community_summaries` (exact tag membership first,
substring fallback second), and the final answer is synthesized strictly
from what's retrieved. Deliberately not a full GraphRAG implementation —
no embeddings, no hierarchical multi-level community summarization. That
scope was a considered choice for this project's single-user use case, not
a first step assumed to need deepening; see `AGENTS.md`.

`ask` is the one deliberate exception to this project's "add every pipeline
step to both entry points" convention: it has no Prefect equivalent,
because it's an interactive, human-in-the-loop command, not a scheduled
batch step.

### RDF export complements the tag graph, not replaces it

`export-rdf` (see [Exporting an RDF knowledge graph](#exporting-an-rdf-knowledge-graph))
is a separate, on-demand export of the tracking database — not part of
`run`'s pipeline, and not a recomputation of `tag_graph.py`'s per-run
co-occurrence graph. That distinction matters because the two serve
different audiences: `tag_graph.json`/`tag_graph.html` are for a single
run's D3 viewer; the RDF export is for integrating this pipeline's output
into a larger, multi-source knowledge base, where a standard vocabulary
(SKOS/PROV-O/schema.org) matters more than any one run's view. Reusing
standard vocabularies rather than inventing a bespoke schema was a
deliberate choice — the whole point of this export is to avoid the
knowledge base reinventing a graph standard it could just adopt.

Unlike `ask` (which has no Prefect equivalent by design — see
`AGENTS.md`), `export-rdf` does run as part of `daily_report_flow` — it's
that flow's last task, so a fresh export lands automatically at the end of
every scheduled run, right after `tag_graph.json`/`tag_graph.html` are
written. There's still only one Prefect flow/deployment
(`daily-strategic-report`) to operate; `export-rdf` on the CLI remains a
separate, on-demand command for ad-hoc use — see [Exporting an RDF
knowledge graph](#exporting-an-rdf-knowledge-graph).

### `validate-feeds` maintains the feed configs, it isn't a pipeline step

RSS feeds rot: hosts disappear, blogs migrate platforms, WAFs start blocking
naive HTTP clients, XML gets served with syntax errors. `validate-feeds`
(see [Validating RSS feeds](#validating-rss-feeds)) checks every feed across
every topic and, with `--fix`, prunes the dead ones out of their
`feeds_*.json` file. It's deliberately a maintenance utility run on demand,
not folded into `run` or the Prefect flow — feed health doesn't change
day to day the way news does, so checking it on every scheduled run would
just be wasted network calls against sources that were fine yesterday.

Dead feeds are logged in `data/rss_feeds/REMOVED.json`, in the same
`{"title", "url", "exception"}` shape already used for the handful of
feeds removed by hand before this command existed (which use a `"reason"`
field instead, e.g. "I do not want personal websites." — `validate-feeds`
never overwrites those, only appends). Re-running `--fix` periodically
accumulates history in that file rather than replacing it.

### Observability

Every LLM call is traced via [Langfuse](https://langfuse.com) or
[Phoenix](https://phoenix.arize.com) (see [Tracing](#tracing) below). A
`run_id` UUID is generated per pipeline run and attached to all LLM calls as
metadata, so a full run appears as a single trace in Langfuse — latency,
token counts, and cost visible at a glance.

---

## Quick start

```bash
pip install -e .
```

The `flow` extra adds Prefect (see [Scheduling with Prefect](#scheduling-with-prefect)); `test` adds pytest; `lint` adds ruff; `typecheck` adds mypy:

```bash
pip install -e ".[flow,test,lint,typecheck]"
```

Set your model and credentials:

```bash
# Ollama (local)
export LLM_MODEL="ollama_chat/llama3.1:70b"

# Ollama (hosted on a remote server)
export LLM_MODEL="ollama_chat/llama3.1:70b"
export OLLAMA_API_BASE="http://your-server-ip:11434"

# or Claude
export LLM_MODEL="anthropic/claude-sonnet-4-6"
export ANTHROPIC_API_KEY="..."

# or OpenAI
export LLM_MODEL="gpt-4o"
export OPENAI_API_KEY="..."
```

Start a local PostgreSQL instance for the tracking database (or point
`DATABASE_URL` at an existing one) and apply the schema migrations:

```bash
docker compose up -d                      # starts postgres:16 on localhost:5432
export DATABASE_URL="postgresql://postgres:postgres@localhost:5432/strategic_reports"
python -m strategic_reports.daily.cli db upgrade
```

`DATABASE_URL` can also go in a gitignored `.env` file (see `.env.example`)
instead of `export`ing it — both `cli.py` and `flows/daily_report.py` load
it automatically via `python-dotenv`.

Run, specifying where the report gets written (required — the tracking
database connection comes from `--database-url`/`DATABASE_URL` above). The
CLI has five commands (`run`, `ask`, `export-rdf`, `validate-feeds`, and
`db upgrade` — see [Asking questions about the archive](#asking-questions-about-the-archive),
[Exporting an RDF knowledge graph](#exporting-an-rdf-knowledge-graph), and
[Validating RSS feeds](#validating-rss-feeds)), so naming one explicitly is
required:

```bash
python -m strategic_reports.daily.cli run \
  --output-dir output/daily/strategic-report
```

Open `index.html` in the output directory in a browser to read the report.

---

## Configuration

Most options can be set via CLI flag or environment variable and have a
default. `--output-dir` is the exception: it's required with no default and
no env var. `--database-url` is also required, but does have an env var
(`DATABASE_URL`) — see [Quick start](#quick-start).

| Flag | Env var | Default | Description |
|------|---------|---------|-------------|
| `--output-dir` | — | *(required)* | HTML output directory — wiped and recreated on every run |
| `--model` | `LLM_MODEL` | `ollama_chat/glm-5.2:cloud` | litellm model string |
| `--hours-cutoff` | — | `24` | Article age window in hours |
| `--data-dir` | `STRATEGIC_REPORTS_DATA_DIR` | bundled `rss_feeds/` package data | RSS feed config directory |
| `--database-url` | `DATABASE_URL` | *(required)* | PostgreSQL tracking database URL — persists across runs. Schema must already be migrated (`strategic-reports db upgrade`); not applied implicitly on connect. |
| `--batch-size` | — | `50` | Articles per LLM summarization call |
| `--max-concurrent` | — | `3` | Max topics hitting the LLM API simultaneously |
| `--temperature` | — | `0.1` | LLM sampling temperature |
| `--instructor-mode` | — | `TOOLS` | Structured output mode (see below) |
| `--ollama-api-base` | `OLLAMA_API_BASE` | — | Ollama server URL (e.g. `http://my-server:11434`) |
| `--ollama-api-key` | `OLLAMA_API_KEY` | — | API key for authenticated Ollama instances |
| `--absolute-threshold` | — | `0.8` | Urgency score (0–1) above which an alert fires unconditionally |
| `--z-score-threshold` | — | `2.0` | Standard deviations above a topic's historical mean urgency score that trigger a statistical alert (requires ≥7 prior runs for that topic) |
| `--tag-z-score-threshold` | — | `2.0` | Standard deviations above a tag's historical mean rate (tag count ÷ articles considered) that trigger an emerging-tag alert (requires ≥7 prior runs for that tag) |
| `--log-level` | — | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR` |

`python -m strategic_reports.daily.cli run` performs the full pipeline — RSS
ingestion, per-topic summarization/strategy, cross-topic synthesis, urgency
alerting, bullet diffing, HTML rendering, and the tag co-occurrence graph —
the same steps the [Prefect flow](#scheduling-with-prefect) runs on a
schedule.

### `--instructor-mode`

Controls how [instructor](https://github.com/jxnl/instructor) requests structured JSON output from the model:

| Mode | When to use |
|------|-------------|
| `TOOLS` (default) | Models with native tool/function-calling support — OpenAI, Anthropic, and most capable Ollama models |
| `JSON` | Ollama models that **don't** support tool calling (e.g. `gpt-oss:120b`). Instructor injects the schema into the system prompt and expects raw JSON back. |
| `MD_JSON` | Models that wrap their JSON output in ` ```json ``` ` fences instead of returning it bare. |

If you see the error `No tool calls or function call found in response (mode: TOOLS)`, switch to `--instructor-mode JSON`.

Example — run against Claude with higher concurrency and debug logging:

```bash
python -m strategic_reports.daily.cli run \
  --output-dir output/daily/strategic-report \
  --model anthropic/claude-sonnet-4-6 \
  --max-concurrent 5 \
  --log-level DEBUG
```

Example — run against an Ollama model without tool-calling support:

```bash
python -m strategic_reports.daily.cli run \
  --output-dir output/daily/strategic-report \
  --model ollama_chat/gpt-oss:120b \
  --instructor-mode JSON
```

---

## Asking questions about the archive

`python -m strategic_reports.daily.cli ask "<question>"` lets you ask a
free-text question about everything the pipeline has archived so far —
not just today's report:

```bash
python -m strategic_reports.daily.cli ask \
  "What's happening with export controls?"
```

This is **graph-guided retrieval, not full GraphRAG**: it extracts a
handful of candidate tags from your question, matches them against the
LLM-written summaries `run` writes for each Louvain tag-community every day
(`community_summaries` — see [Output](#output)), and answers grounded only
in what's retrieved — no embeddings, no hierarchical multi-level community
summarization, no outside knowledge. It's read-only against
`--database-url` and never touches `--output-dir`. See `archive_query.py`
and `pipeline.answer_archive_question()`.

| Flag | Env var | Default | Description |
|------|---------|---------|-------------|
| `question` (positional) | — | *(required)* | Free-text question about the archive |
| `--database-url` | `DATABASE_URL` | *(required)* | PostgreSQL tracking database to query — the same one `run` writes to |
| `--max-communities` | — | `8` | Max matching archived tag-communities to include as retrieved context |
| `--model`, `--temperature`, `--instructor-mode`, `--ollama-api-base`, `--ollama-api-key`, `--log-level` | — | same as `run` | See [Configuration](#configuration) |

If nothing archived matches the question (including on a brand-new,
still-empty archive), it says so plainly rather than guessing.

---

## Exporting an RDF knowledge graph

`python -m strategic_reports.daily.cli export-rdf` exports the accumulated
archive as an RDF (Turtle) knowledge graph — intended for integration into
a broader, multi-source knowledge base, not as a replacement for anything
else this pipeline produces:

```bash
python -m strategic_reports.daily.cli export-rdf \
  --output output/daily/knowledge_graph.ttl
```

This **complements `tag_graph.py`'s per-run co-occurrence JSON/HTML output
— it doesn't replace or recompute it.** `tag_graph.json`/`tag_graph.html`
keep being written exactly as before, per run, for that run's D3 viewer.
`export-rdf` instead reads the durable, cross-run archive already in
`--database-url` (articles, tags, community summaries, bridge tags, per-topic
strategic bullets, urgency scores, cross-topic overviews — see
[Output](#output)) and gives it a standard, portable RDF shape:

- **SKOS** — tags are `skos:Concept`; Louvain communities are
  `skos:Collection` with `skos:member` tags. Tags are already normalized to
  a canonical form by `tag_normalizer.py` before they reach the database,
  so this mostly formalizes an existing vocabulary rather than modeling
  something new.
- **PROV-O** — every fact traces back to the run (`prov:Activity`) that
  produced it via `prov:wasGeneratedBy`, mirroring the `run_id` foreign key
  already threaded through every table in `db.py`.
- **schema.org** — article bibliographic fields (`headline`, `url`,
  `datePublished`).
- A small custom namespace (`stratrep:`) for what's genuinely
  domain-specific and has no standard equivalent: topics, urgency scores,
  bridge-tag observations, and the cross-topic overview.

| Flag | Env var | Default | Description |
|------|---------|---------|-------------|
| `--database-url` | `DATABASE_URL` | *(required)* | PostgreSQL tracking database to export — the same one `run` writes to |
| `--output` | — | *(required)* | Turtle (`.ttl`) file to write the export to |
| `--since` | — | *(none — full rebuild)* | Only include runs at or after this point — a `run_id` or an ISO 8601 timestamp. With no cutoff, rebuilds from every run in the database. |

`--since` only filters which runs are included in this export — it does
**not** merge into an existing `.ttl` file. Each invocation writes a fresh
file at `--output`; combining a full export with later incremental exports
(e.g. loading multiple `.ttl` files into a triple store) is left to
whatever tool consumes them. Read-only against `--database-url`; never
touches `--output-dir`. See `rdf_export.py`.

On the CLI, this remains a third command, like `ask` — a deliberate
exception to the run/flow parity convention (`run` does not export RDF —
see `AGENTS.md`).

The Prefect *flow* is the other side of that exception: `export-rdf` runs
there automatically as `daily_report_flow`'s last task (see [Flow
structure](#flow-structure)), on every scheduled run — no separate flow
file, deployment, or process to operate. It always does a full rebuild (no
`--since` watermark tracking on the automatic run, same v1 scope choice as
the CLI command) and writes to `knowledge_graph.ttl` next to `output_dir`
(`output_dir`'s parent directory), since `--output-dir` itself is wiped
and recreated on every run and can't hold anything durable — there's no
local tracking-db directory to piggyback on anymore now that the tracking
database is PostgreSQL, not a SQLite file. It fails
gracefully — a failed export logs a warning but doesn't affect the HTML
report, which has already rendered by the time this task runs.

---

## Validating RSS feeds

`python -m strategic_reports.daily.cli validate-feeds` checks every feed
across every configured topic and reports which ones are dead — a fetch
exception (DNS failure, timeout, connection refused, SSL handshake
failure), an XML parse error with zero salvageable entries, or an HTTP 200
response with zero parseable entries:

```bash
python -m strategic_reports.daily.cli validate-feeds
```

By default this only reports. Pass `--fix` to prune the failing feeds out
of their `feeds_*.json` file and log them in `REMOVED.json`, next to any
manually-curated exclusions already recorded there (see
[`validate-feeds` maintains the feed configs, it isn't a pipeline
step](#validate-feeds-maintains-the-feed-configs-it-isnt-a-pipeline-step)):

```bash
python -m strategic_reports.daily.cli validate-feeds --fix
```

| Flag | Env var | Default | Description |
|------|---------|---------|-------------|
| `--data-dir` | `STRATEGIC_REPORTS_DATA_DIR` | bundled `rss_feeds/` package data | Directory containing `rss_feeds/*.json` files |
| `--fix` | — | `False` | Remove failing feeds from their category files and log them in `REMOVED.json`. Without this flag, only reports failures. |

Safe to re-run periodically: feeds that already pass are left untouched,
and `--fix` appends to `REMOVED.json` rather than overwriting it. Not part
of `run` or the Prefect flow — see the design-decisions note linked above
for why.

---

## Scheduling with Prefect

The pipeline ships with a [Prefect](https://www.prefect.io) flow that runs automatically at **16:00 America/Los_Angeles, Monday through Friday** (no weekend runs). It uses a local Prefect server — no cloud account required.

### Why Prefect?

| Capability | What it gives you |
|------------|------------------|
| Scheduled runs | Set the cron once; Prefect triggers it automatically |
| Run history | Every run is recorded — status, duration, logs, token counts |
| Task-level status | See whether a failure was in config load, LLM pipeline, or rendering |
| Automatic retries | Transient LLM API errors are retried (2×, 60s apart) without any extra code |
| Parameter overrides | Trigger a one-off run with a different model or hours cutoff from the UI or CLI |

### Setup

**1. Start the local Prefect server** (keep this terminal open):

```bash
prefect server start
```

The UI is available at **http://localhost:4200**.

**2. Point the Prefect client at the local server:**

```bash
prefect config set PREFECT_API_URL=http://localhost:4200/api
```

This only needs to be run once — the setting is persisted in your Prefect profile.

**3. Set environment variables** for your Ollama instance, model, and tracking database:

```bash
export LLM_MODEL="ollama_chat/gpt-oss:120b"
export OLLAMA_API_BASE="http://your-ollama-server:11434"
export OLLAMA_API_KEY="your-key-if-required"   # omit if not needed
export DATABASE_URL="postgresql://user:pass@host:5432/strategic_reports"
```

`DATABASE_URL` must already be migrated (`strategic-reports db upgrade` —
see [Quick start](#quick-start)) before the first scheduled run; schema
creation is no longer implicit on connect the way it was under SQLite.

These can also be placed in a `.env` file — both this scheduler process
and `cli.py` load it automatically via `python-dotenv`, so `source .env`
isn't strictly required, though it still works for a one-off shell. In
production, referenced via the `EnvironmentFile` directive in the systemd
unit below.

**4. Start the scheduler** (keep this terminal open):

```bash
python -m strategic_reports.daily.flows.daily_report
```

This registers the deployment with the local server and polls for scheduled runs. The flow defaults to `instructor_mode=TOOLS` (override to `JSON` if your model doesn't support tool calling — see `--instructor-mode` below) and reads `OLLAMA_API_BASE` / `OLLAMA_API_KEY` from the environment automatically. An invalid `instructor_mode` value fails the flow run immediately, before any task runs.

The process must stay alive — run it under `systemd` or in a `tmux`/`screen` session.

Example `systemd` unit (`/etc/systemd/system/strategic-reports.service`):

```ini
[Unit]
Description=Strategic Reports Prefect scheduler
After=network.target

[Service]
User=<your-user>
WorkingDirectory=<project-root>
EnvironmentFile=<project-root>/.env
ExecStart=/path/to/venv/bin/python -m strategic_reports.daily.flows.daily_report
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

There is only this one systemd unit / Prefect deployment to run —
`export-rdf` executes as `daily_report_flow`'s last task (see [Exporting
an RDF knowledge graph](#exporting-an-rdf-knowledge-graph)), not as a
separate process.

**5. Trigger a one-off run immediately** (optional, from a separate terminal):

```bash
prefect deployment run 'daily-strategic-report/daily-strategic-report'
```

**6. Override parameters** for a one-off run:

```bash
prefect deployment run 'daily-strategic-report/daily-strategic-report' \
    --param hours_cutoff=48 \
    --param model=anthropic/claude-sonnet-4-6 \
    --param instructor_mode=TOOLS
```

### Flow structure

The flow contains twelve tasks, each tracked independently in the Prefect UI:

```
daily_report_flow
  ├── build-topic-configs         (sync)   load feed JSON configs from data_dir
  ├── run-llm-pipeline            (async)  RSS ingestion + LLM summarization + synthesis
  │                                        retries=2, retry_delay=60s
  ├── run-cross-topic-synthesis   (async)  single LLM call across all topic insights
  │                                        retries=2; fails gracefully to None
  ├── archive-articles            (sync)   persist article summaries (source material)
  │                                        inserts into --database-url (articles/article_summary_bullets/article_tags)
  ├── check-urgency-alerts        (sync)   score each topic; alert if above threshold
  │                                        inserts into --database-url (urgency_scores table)
  ├── check-emerging-tags         (sync)   compare today's tag rates vs. each tag's baseline
  │                                        inserts into --database-url (tag_counts/tag_topics/tag_edges)
  ├── summarize-communities       (async)  one LLM call per Louvain tag-community
  │                                        retries=2; fails gracefully, continues without summaries
  │                                        inserts into --database-url (community_summaries/community_summary_tags)
  ├── run-bullet-diff             (async)  diff today's bullets vs. yesterday's per topic
  │                                        retries=2; fails gracefully to {}
  │                                        skipped (no diff) on first run
  │                                        inserts into --database-url (bullets table)
  ├── compute-systems-signals     (sync)   lagged correlation over topic urgency + tag coverage
  │                                        history (see Design decisions); fails gracefully to
  │                                        ([], []); read-only, no inserts
  ├── render-html-report          (sync)   Jinja2 → HTML output files
  ├── build-tag-graph             (sync)   tag co-occurrence graph → tag_graph.json + tag_graph.html
  └── export-rdf                  (sync)   full-rebuild RDF (Turtle) export → knowledge_graph.ttl next to output_dir
                                            fails gracefully; no --since watermark tracking
```

Flow parameters share names with their CLI-flag counterparts (e.g.
`absolute_threshold` ↔ `--absolute-threshold` — see
[Configuration](#configuration)); override any of them from the Prefect UI
or via `--param`.

---

## Tracing

Neither backend is required. The pipeline runs normally without them.

### Langfuse (production tracing)

Set credentials and all LLM calls are automatically logged as generations:

```bash
export LANGFUSE_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_SECRET_KEY="sk-lf-..."
export LANGFUSE_HOST="https://cloud.langfuse.com"   # optional; default shown
```

Each pipeline run groups under a single trace in the Langfuse UI, labeled by
`run_id`. You can inspect token costs, latency per topic, and prompt/response
content for every call.

### Phoenix (local debugging)

Starts a local Phoenix server at `http://localhost:6006` and instruments all
LLM calls via OpenTelemetry. Useful during development for inspecting exactly
what the model received and returned.

```bash
pip install arize-phoenix openinference-instrumentation-litellm \
            opentelemetry-sdk opentelemetry-exporter-otlp-proto-http

export PHOENIX_TRACING=true
python -m strategic_reports.daily.cli run \
  --output-dir output/daily/strategic-report
```

---

## Running the tests

```bash
pytest
ruff check .
mypy
```

293 tests across 17 files. No real API calls — the LLM client is fully mocked.
DB-touching tests need a reachable PostgreSQL instance (`DATABASE_URL`) —
run `docker compose up -d` first for local dev (see [Quick
start](#quick-start)). A GitHub Actions workflow
(`.github/workflows/tests.yml`) runs `pytest`/`lint`/`typecheck` as
separate jobs on every push and pull request to `main`; only the `pytest`
job has a Postgres service container (`lint`/`typecheck` are static
analysis, no DB needed) — no LLM credentials needed anywhere. `mypy` runs
in `--strict` mode across both `strategic_reports/` and `tests/`.

```
tests/test_models.py      Pydantic validation and TokenUsage arithmetic
tests/test_prompts.py     Prompt builder output shape and content
tests/test_renderer.py    HTML rendering for all three result states + XSS; Systems Signals section (empty state, both-and-neither signal groups, lag=0 bidirectional dedup, XSS)
tests/test_ingestion.py   RSS fetching with mocked feedparser
tests/test_feed_validation.py  Feed health checks and REMOVED.json pruning/logging, with mocked feedparser
tests/test_pipeline.py    Async orchestration with mocked LLMClient; summarize_communities() and answer_archive_question()/extract_query_tags() concurrency + failure isolation
tests/test_urgency.py     Urgency alerting: absolute threshold, z-score, std==0 fallback, history persistence, per-row failure isolation (savepoints) for append_run
tests/test_bullet_diff.py Bullet-history storage: most-recent-run lookup, ordering, multi-topic, per-row failure isolation (savepoints) for append_bullet_run
tests/test_db.py          Tracking-db safety guard, schema creation, run registration, shared insert_rows_isolating_failures helper
tests/test_tag_tracking.py  Tag-graph db round-trip, rate-history normalization, emerging-tag z-score, bridge-tag/community-summary audit trails, per-row failure isolation (savepoints) for record_tags/record_community_summaries
tests/test_tag_graph.py    find_bridge_tags(), group_articles_by_community(): filtering, sorting, dedup, multi-community span
tests/test_article_archive.py  Article-summary db round-trip, ordering, multi-topic, error/empty topics, per-article failure isolation (savepoints)
tests/test_archive_query.py  find_relevant_communities(): exact/substring matching, dedup, ordering, limit
tests/test_tag_normalizer.py  Tag synonym normalization
tests/test_overview_archive.py  Cross-topic overview bullet round-trip, ordering, multi-run
tests/test_rdf_export.py  RDF ontology mapping (articles/tags/communities/bridge tags/urgency/bullets/overview), --since filtering, Turtle serialization
tests/test_systems_signals.py  Lagged/partial-correlation math, Benjamini-Hochberg FDR correction, all five tag-rate filters (sparsity, containment, community, topic-volume, single-source), topic-urgency leave-one-out trend control, article-archive-gap handling for both series — pure-function tests against synthetic series, no DB
```

---

## Project structure

```
strategic_reports/
  py.typed             PEP 561 marker — this package ships inline type hints
  daily/
    core/
      models.py          Pydantic data models (RawArticle → ArticleSummary → TopicResult → CrossTopicSynthesis → BulletDiff)
      llm_client.py      Async LLMClient: litellm + instructor + tenacity retry
      ingestion.py       Async RSS fetching; returns list[RawArticle]
      feed_validation.py Async RSS feed health checks + REMOVED.json pruning/logging for the `validate-feeds` CLI command
      prompts.py         System messages and user-message builder functions
      pipeline.py        Two-phase async orchestrator + cross-topic synthesis + summarize_communities() + answer_archive_question()
      renderer.py        Jinja2 HTML rendering
      tag_normalizer.py  Tag synonym map and normalize_tags(); applied via Pydantic validator
      tag_graph.py       Tag co-occurrence graph builder; full tag_graph.json + pruned/community tag_graph_display.json + tag_graph.html; find_bridge_tags(), group_articles_by_community()
      urgency.py         Urgency alert logic: absolute threshold + z-score baseline (PostgreSQL-backed)
      bullet_diff.py     Historical bullet diffing: load/append history, concurrent per-topic LLM diff (PostgreSQL-backed)
      db.py              PostgreSQL tracking database: pooled connection helper, reachability check, run registration, shared insert_rows_isolating_failures helper (schema lives in alembic/)
      article_archive.py Persists each run's article summaries (source material), linked to run_id
      overview_archive.py  Persists each run's cross-topic synthesis overview bullets, linked to run_id
      archive_query.py   Graph-guided retrieval: find_relevant_communities() for the `ask` CLI command
      tag_tracking.py    Per-run tag-graph persistence (linked to run_id) + emerging-tag z-score alerting + community-summary persistence
      systems_signals.py Lagged/partial correlation over topic urgency + tag coverage rate history; five-filter tag-rate chain + FDR correction — read-only, nothing persisted
      rdf_export.py      Builds an RDF (Turtle) export of the tracking database for the `export-rdf` CLI command
      tracing.py         Langfuse and Phoenix setup (opt-in)
    templates/
      base.html.j2       Shared layout and styles
      index.html.j2      Main strategic report (Strategic Overview + Systems Signals + per-topic sections)
      topic.html.j2      Per-topic article summaries
    data/
      rss_feeds/         One JSON file per topic listing RSS feed URLs — packaged as wheel data
        REMOVED.json     Audit log of feeds removed by `validate-feeds --fix` or by hand
    flows/
      daily_report.py    Prefect flow (see Scheduling with Prefect) — twelve tasks, including export-rdf as the last one
    config/
      topic_order.py     Ordered list of topic slugs and display titles
    cli.py               typer CLI entrypoint
    paths.py             default_data_dir() — resolves the bundled rss_feeds/ via importlib.resources
alembic/                Tracking-db schema migrations (Postgres) — versions/0001_initial_schema.py is the whole schema, hand-written
tests/
  conftest.py          Shared fixtures and feedparser mock helpers, incl. the database_url fixture
  test_*.py            Per-module test files
blog-posts/            Human-written companion articles about this project — not code, not shipped
pyproject.toml         Package metadata, dependencies, and the `strategic-reports` console script
docker-compose.yml      Local-dev PostgreSQL for the test suite / manual runs
.env.example            Template for a gitignored .env (DATABASE_URL, LLM config)
```

---

## Output

> **`--output-dir` is wiped on every run.** The directory (if it exists) is
> deleted and recreated from scratch before any files are written, so stale
> pages from a previous run never linger. Point it at a directory dedicated
> to this pipeline's output — never at a directory containing anything else
> you care about.

The pipeline writes the following files to `--output-dir`:

- **`index.html`** — the main report. Opens with a highlighted **Strategic Overview** section (3–4 cross-cutting bullets synthesized across all topics), followed by a **Systems Signals** section (candidate feedback loops from lagged correlation — see [Design decisions](#systems-signals-surfaces-candidate-feedback-loops-filtered-aggressively) — grouped into Topic Urgency and Tag Coverage results, each with its correlation, lag, and q-value; shown even when empty, with a plain "no candidate feedback loops cleared significance today" message rather than disappearing), followed by one section per topic with 3–5 strategic bullet points. On runs after the first, each topic also shows a **Since yesterday** annotation: new bullets highlighted in green, dropped bullets in muted strikethrough. Errors and empty topics are surfaced inline rather than hidden.
- **`{topic}_summaries.html`** — per-article summaries and tags for every article that fed into that topic's strategic synthesis. Each article is also marked up as a `schema:Article` JSON-LD block (`headline`/`url`/`datePublished`) — the same fields `export-rdf` maps onto `schema:Article` — for structured-data consumers (search engines, scrapers) that only have access to the HTML, not `--database-url`.
- **`tag_graph.html`** — interactive D3.js force-directed graph of tag co-occurrences. Self-contained: graph data is inlined at build time so it opens directly from the filesystem (`file://`) without a web server. Node color = Louvain community cluster; node size = article count; edge thickness = co-occurrence count. Sliders allow further filtering by minimum co-occurrence and minimum article count. Hovering a node shows its community label (named after the highest-count tag in that cluster).
- **`tag_graph_display.json`** — pruned and community-annotated graph consumed by `tag_graph.html`. Nodes with fewer than 3 article appearances and edges with fewer than 2 co-occurrences are dropped before Louvain community detection runs. Typically ~200 nodes and ~800 edges.
- **`tag_graph.json`** — full graph (all tags and co-occurrence edges, unfiltered) for downstream data science use.

Cross-run history is kept separately, in the PostgreSQL database at
`--database-url` (see [Configuration](#configuration)):

- **`runs`** — one row per pipeline run: `run_id`, `created_at` timestamp, and `article_count` (total articles considered that run — the denominator for comparing tag weights across runs, since a raw tag count means something different on a 400-article day than a 50-article one).
- **`articles`**, **`article_summary_bullets`**, **`article_tags`** — every article's title, link, publish date, summary bullets, and tags, linked to `run_id`. This is the source material every derived signal below (tags, bullets, urgency scores) is computed from — otherwise it exists only in memory during a run and is lost once `{topic}_summaries.html` (in the wiped `--output-dir`) is gone. Each article's insert is isolated in its own savepoint (`article_archive.record_articles`), so one bad row only loses that article, not the whole run's archive. Not currently read by `ask` (which reads `community_summaries` instead) — read by Systems Signals' topic-volume control, single-source filter, and (via a distinct-`run_id` presence check) both series' article-archive-gap handling (see [Design decisions](#systems-signals-surfaces-candidate-feedback-loops-filtered-aggressively)), and available for a future retrieval mode grounded in raw article text.
- **`urgency_scores`** — one row per topic per run; used by the z-score baseline after 7 runs per topic. Each row's insert (`urgency.append_run`) is isolated the same way as `articles`/`tag_counts` above.
- **`bullets`** — one row per strategic bullet per topic per run; used by the bullet diff to identify what changed since the most recent prior run. Each row's insert (`bullet_diff.append_bullet_run`) is isolated the same way.
- **`tag_counts`**, **`tag_topics`**, **`tag_edges`** — one run's tag graph (per-tag counts, per-tag topic membership, and tag-pair co-occurrence edges), linked to `run_id`. Together these let `tag_graph.json` be reconstructed for any past run directly from the database, and are what `systems_signals.tag_rate_lagged_correlations()` reads its rate series and candidate pairs from. `tag_counts` also backs the emerging-tag z-score alert: a tag's rate (count ÷ that run's `article_count`) is compared against its own historical rate once it has 7+ prior runs; tags with less history (including brand-new tags) are skipped rather than guessed at, since — unlike urgency scores — tag rates have no meaningful absolute cutoff to fall back on. Like `articles` above, each row's insert (`tag_tracking.record_tags`) is isolated — batched for speed, falling back to one-row-at-a-time savepoints only if the batch fails, since a single run can produce tens of thousands of `tag_edges` rows.
- **`emerging_tag_alerts`** — an audit trail of the alerts that actually fired: `tag`, `count`, `rate`, `mean`, `std`, `z_score`, linked to `run_id`. Only fired alerts are stored here, not every tag's rate/z-score every run — those stay recomputable on demand from `tag_counts` + `runs.article_count`.
- **`bridge_tags`**, **`bridge_tag_topics`** — an audit trail of the bridge tags (`tag_graph.find_bridge_tags()`) actually surfaced to the cross-topic synthesis prompt each run: `tag`, `count`, `rank`, and each tag's topic list, linked to `run_id`. Answers "which tags did we point the synthesis at on day N" directly, without recomputing from `results`.
- **`community_summaries`**, **`community_summary_tags`** — an LLM-written paragraph per Louvain tag-community (`label`, `summary`, `article_count`, and each community's member tags), linked to `run_id`. Grounded in the articles whose tags belong to that community; replaces "labeled by top tag" with real substance. `community_summary_tags` is also what `systems_signals.tag_rate_lagged_correlations()`'s community filter reads for topical-adjacency checks (see [Design decisions](#systems-signals-surfaces-candidate-feedback-loops-filtered-aggressively)) — the Louvain clustering itself comes from `tag_graph.py`, not an LLM call, even though it's persisted alongside the LLM-written summary text.
- **`cross_topic_overviews`** — one row per Strategic Overview bullet per run, linked to `run_id`. The cross-topic synthesis is rendered into `index.html` but was otherwise never persisted anywhere — this is what `export-rdf` (see [Exporting an RDF knowledge graph](#exporting-an-rdf-knowledge-graph)) reads to include it in the knowledge graph.

Every row carries its own `created_at` timestamp in addition to `run_id`, and
nothing is pruned — unlike the JSON files this replaced, which capped bullet
history at the last 7 runs, the database keeps full history indefinitely.
Systems Signals (see [Design decisions](#systems-signals-surfaces-candidate-feedback-loops-filtered-aggressively))
reads across several of the tables above but doesn't add one of its own —
its output is recomputed fresh on every run, not persisted.

Weekend runs will produce thinner output — most news sources don't publish on weekends.

---

## License

[MIT](LICENSE)
