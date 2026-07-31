# Strategic Reports Pipeline

[![Tests](https://github.com/badass-data-science/strategic-report-generator/actions/workflows/tests.yml/badge.svg)](https://github.com/badass-data-science/strategic-report-generator/actions/workflows/tests.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue)](.github/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

A daily briefing pipeline that reads recent news across 12 topic feeds — AI,
biotech, economics, geopolitics, defense, and more — and synthesizes strategic
recommendations into a linked HTML report.

> **Caveat emptor.** LLM-generated strategic analysis is a starting point, not
> a substitute for human judgment. Apply your own reasoning and follow-up
> research before acting on any recommendation.

> Working on this repo with an AI coding agent? See [`AGENTS.md`](AGENTS.md)
> for setup, test commands, and code conventions.

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

  list[TopicResult] ──► CrossTopicSynthesis  (3–4 cross-cutting bullets)

Phase 4 — Historical Diffing  [concurrent per-topic LLM calls]

  --db-path (bullets table) ──► yesterday's bullets (per topic)
  list[TopicResult]         ──► diff vs. yesterday ──► dict[topic, BulletDiff]
                                                        (new / continued / dropped)
  (skipped on first run; today's bullets inserted into --db-path after diff)

Phase 5 — Rendering  [Jinja2 templates]

  CrossTopicSynthesis ──► Strategic Overview section (top of index.html)
  dict[topic, BulletDiff] ──► "Since yesterday" annotations per topic
  list[TopicResult]   ──► index.html  (per-topic sections)
                      ──► {topic}_summaries.html  (one per topic)
                      ──► tag_graph.html + tag_graph.json  (D3.js tag network)

Phase 6 — Upload  [SCP + SSH]

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
pure network I/O with no rate limits — all 12 topics' feeds fire concurrently
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

### Observability

Every LLM call is traced via [Langfuse](https://langfuse.com) or
[Phoenix](https://phoenix.arize.com) (see [Tracing](#tracing) below). A
`run_id` UUID is generated per pipeline run and attached to all LLM calls as
metadata, so a full run appears as a single trace in Langfuse — latency,
token counts, and cost visible at a glance.

---

## Quick start

```bash
pip install -r requirements.txt
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

Run, specifying where the report gets written and where the tracking
database lives (both required):

```bash
python -m strategic_reports.daily.cli \
  --output-dir output/daily/strategic-report \
  --db-path output/daily/strategic_reports.db
```

Open `index.html` in the output directory in a browser to read the report.

---

## Configuration

Most options can be set via CLI flag or environment variable and have a
default. `--output-dir` and `--db-path` are the exception: both are required.

| Flag | Env var | Default | Description |
|------|---------|---------|-------------|
| `--output-dir` | — | *(required)* | HTML output directory — wiped and recreated on every run |
| `--model` | `LLM_MODEL` | `ollama_chat/glm-5.2:cloud` | litellm model string |
| `--hours-cutoff` | — | `24` | Article age window in hours |
| `--data-dir` | `STRATEGIC_REPORTS_DATA_DIR` | `data/rss_feeds` | RSS feed config directory |
| `--db-path` | — | *(required)* | SQLite tracking database — created on first use if missing, persists across runs, never wiped. Must not be inside `--output-dir` (checked at startup). |
| `--batch-size` | — | `50` | Articles per LLM summarization call |
| `--max-concurrent` | — | `3` | Max topics hitting the LLM API simultaneously |
| `--temperature` | — | `0.1` | LLM sampling temperature |
| `--instructor-mode` | — | `TOOLS` | Structured output mode (see below) |
| `--ollama-api-base` | `OLLAMA_API_BASE` | — | Ollama server URL (e.g. `http://my-server:11434`) |
| `--ollama-api-key` | `OLLAMA_API_KEY` | — | API key for authenticated Ollama instances |
| `--absolute-threshold` | — | `0.8` | Urgency score (0–1) above which an alert fires unconditionally |
| `--z-score-threshold` | — | `2.0` | Standard deviations above a topic's historical mean urgency score that trigger a statistical alert (requires ≥7 prior runs for that topic) |
| `--log-level` | — | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR` |

`python -m strategic_reports.daily.cli` performs the full pipeline — RSS
ingestion, per-topic summarization/strategy, cross-topic synthesis, urgency
alerting, bullet diffing, HTML rendering, and the tag co-occurrence graph —
the same steps the [Prefect flow](#scheduling-with-prefect) runs on a
schedule. The one thing the CLI does *not* do is the optional remote
upload step, which is Prefect-only.

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
python -m strategic_reports.daily.cli \
  --output-dir output/daily/strategic-report \
  --db-path output/daily/strategic_reports.db \
  --model anthropic/claude-sonnet-4-6 \
  --max-concurrent 5 \
  --log-level DEBUG
```

Example — run against an Ollama model without tool-calling support:

```bash
python -m strategic_reports.daily.cli \
  --output-dir output/daily/strategic-report \
  --db-path output/daily/strategic_reports.db \
  --model ollama_chat/gpt-oss:120b \
  --instructor-mode JSON
```

---

## Scheduling with Prefect

The pipeline ships with a [Prefect](https://www.prefect.io) flow that runs automatically at **00:30 America/Los_Angeles** daily. It uses a local Prefect server — no cloud account required.

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

**3. Set environment variables** for your Ollama instance and model:

```bash
export LLM_MODEL="ollama_chat/gpt-oss:120b"
export OLLAMA_API_BASE="http://your-ollama-server:11434"
export OLLAMA_API_KEY="your-key-if-required"   # omit if not needed
```

These can also be placed in a `.env` file and loaded with `source .env`, or referenced via the `EnvironmentFile` directive in the systemd unit below.

**4. Start the scheduler** (keep this terminal open):

```bash
cd <project-root>
python flows/daily_report.py
```

This registers the deployment with the local server and polls for scheduled runs. The flow defaults to `instructor_mode=JSON` and reads `OLLAMA_API_BASE` / `OLLAMA_API_KEY` from the environment automatically.

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
ExecStart=/path/to/venv/bin/python flows/daily_report.py
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

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

The flow contains eight tasks, each tracked independently in the Prefect UI:

```
daily_report_flow
  ├── build-topic-configs         (sync)   load feed JSON configs from data_dir
  ├── run-llm-pipeline            (async)  RSS ingestion + LLM summarization + synthesis
  │                                        retries=2, retry_delay=60s
  ├── run-cross-topic-synthesis   (async)  single LLM call across all topic insights
  │                                        retries=2; fails gracefully to None
  ├── check-urgency-alerts        (sync)   score each topic; alert if above threshold
  │                                        inserts into --db-path (urgency_scores table)
  ├── run-bullet-diff             (async)  diff today's bullets vs. yesterday's per topic
  │                                        retries=2; fails gracefully to {}
  │                                        skipped (no diff) on first run
  │                                        inserts into --db-path (bullets table)
  ├── render-html-report          (sync)   Jinja2 → HTML output files
  ├── build-tag-graph             (sync)   tag co-occurrence graph → tag_graph.json + tag_graph.html
  └── upload-to-web-server        (sync)   SCP output to remote host; SSH to move into web root
                                           skipped if upload_enabled=false
```

### Upload parameters

The upload step is enabled by default. All connection details have env-var-backed defaults and can be overridden from the Prefect UI or via `--param`.

| Flow parameter | Env var | Default | Description |
|---|---|---|---|
| `absolute_threshold` | — | `0.8` | Urgency score (0–1) above which an alert fires unconditionally |
| `z_score_threshold` | — | `2.0` | Standard deviations above a topic's historical mean that triggers a statistical alert (requires ≥7 prior runs for that topic) |
| `upload_enabled` | — | `true` | Set to `false` to skip the upload entirely |
| `ssh_key_path` | `SSH_KEY_PATH` | `~/api_keys/keys/emily-bds-key.pem` | Path to the SSH private key |
| `remote_host` | `REMOTE_HOST` | `badassdatascience.com` | Hostname of the web server |
| `remote_user` | `REMOTE_USER` | `ubuntu` | SSH login user |
| `remote_staging_dir` | `REMOTE_STAGING_DIR` | `/home/ubuntu` | Writable landing directory on the remote; `output_dir` is SCP'd here recursively (e.g. `/home/ubuntu/strategic-report/`) |
| `remote_web_dir` | `REMOTE_WEB_DIR` | `/var/www/html/strategic-review-daily` | Web root directory; HTML and JSON files are sudo-copied here from the staged subdirectory |

To skip the upload on a one-off run:

```bash
prefect deployment run 'daily-strategic-report/daily-strategic-report' \
    --param upload_enabled=false
```

To point at a different server:

```bash
prefect deployment run 'daily-strategic-report/daily-strategic-report' \
    --param remote_host=myserver.example.com \
    --param remote_user=deploy \
    --param ssh_key_path=/home/emily/.ssh/mykey.pem \
    --param remote_web_dir=/var/www/html/reports
```

To set the defaults permanently via env vars, add them to your `.env` file or the `EnvironmentFile` in the systemd unit:

```bash
export SSH_KEY_PATH=/home/emily/api_keys/keys/emily-bds-key.pem
export REMOTE_HOST=badassdatascience.com
export REMOTE_USER=ubuntu
export REMOTE_STAGING_DIR=/home/ubuntu
export REMOTE_WEB_DIR=/var/www/html/strategic-review-daily
```

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
python -m strategic_reports.daily.cli \
  --output-dir output/daily/strategic-report \
  --db-path output/daily/strategic_reports.db
```

---

## Running the tests

```bash
pytest
```

115 tests across 9 files. No real API calls — the LLM client is fully mocked.
Runs in under a second. A GitHub Actions workflow
(`.github/workflows/tests.yml`) runs the same suite on every push and pull
request to `main` — no LLM credentials needed there either.

```
tests/test_models.py      Pydantic validation and TokenUsage arithmetic
tests/test_prompts.py     Prompt builder output shape and content
tests/test_renderer.py    HTML rendering for all three result states + XSS
tests/test_ingestion.py   RSS fetching with mocked feedparser
tests/test_pipeline.py    Async orchestration with mocked LLMClient
tests/test_urgency.py     Urgency alerting: absolute threshold, z-score, std==0 fallback, history persistence
tests/test_bullet_diff.py Bullet-history storage: most-recent-run lookup, ordering, multi-topic
tests/test_db.py          Tracking-db safety guard, schema creation, run registration
tests/test_tag_normalizer.py  Tag synonym normalization
```

---

## Project structure

```
strategic_reports/daily/
  core/
    models.py          Pydantic data models (RawArticle → ArticleSummary → TopicResult → CrossTopicSynthesis → BulletDiff)
    llm_client.py      Async LLMClient: litellm + instructor + tenacity retry
    ingestion.py       Async RSS fetching; returns list[RawArticle]
    prompts.py         System messages and user-message builder functions
    pipeline.py        Two-phase async orchestrator + cross-topic synthesis
    renderer.py        Jinja2 HTML rendering
    tag_normalizer.py  Tag synonym map and normalize_tags(); applied via Pydantic validator
    tag_graph.py       Tag co-occurrence graph builder; full tag_graph.json + pruned/community tag_graph_display.json + tag_graph.html
    urgency.py         Urgency alert logic: absolute threshold + z-score baseline (SQLite-backed)
    bullet_diff.py     Historical bullet diffing: load/append history, concurrent per-topic LLM diff (SQLite-backed)
    db.py              SQLite tracking database: schema, connection helper, output_dir/db_path safety guard, run registration
    tracing.py         Langfuse and Phoenix setup (opt-in)
  templates/
    base.html.j2       Shared layout and styles
    index.html.j2      Main strategic report (Strategic Overview + per-topic sections)
    topic.html.j2      Per-topic article summaries
  cli.py               typer CLI entrypoint
  config/
    topic_order.py     Ordered list of topic slugs and display titles
data/
  rss_feeds/           One JSON file per topic listing RSS feed URLs
tests/
  conftest.py          Shared fixtures and feedparser mock helpers
  test_*.py            Per-module test files
```

---

## Output

> **`--output-dir` is wiped on every run.** The directory (if it exists) is
> deleted and recreated from scratch before any files are written, so stale
> pages from a previous run never linger. Point it at a directory dedicated
> to this pipeline's output — never at a directory containing anything else
> you care about.

The pipeline writes the following files to `--output-dir`:

- **`index.html`** — the main report. Opens with a highlighted **Strategic Overview** section (3–4 cross-cutting bullets synthesized across all topics), followed by one section per topic with 3–5 strategic bullet points. On runs after the first, each topic also shows a **Since yesterday** annotation: new bullets highlighted in green, dropped bullets in muted strikethrough. Errors and empty topics are surfaced inline rather than hidden.
- **`{topic}_summaries.html`** — per-article summaries and tags for every article that fed into that topic's strategic synthesis.
- **`tag_graph.html`** — interactive D3.js force-directed graph of tag co-occurrences. Self-contained: graph data is inlined at build time so it opens directly from the filesystem (`file://`) without a web server. Node color = Louvain community cluster; node size = article count; edge thickness = co-occurrence count. Sliders allow further filtering by minimum co-occurrence and minimum article count. Hovering a node shows its community label (named after the highest-count tag in that cluster).
- **`tag_graph_display.json`** — pruned and community-annotated graph consumed by `tag_graph.html`. Nodes with fewer than 3 article appearances and edges with fewer than 2 co-occurrences are dropped before Louvain community detection runs. Typically ~200 nodes and ~800 edges.
- **`tag_graph.json`** — full graph (all tags and co-occurrence edges, unfiltered) for downstream data science use.

Cross-run history is kept separately, in the SQLite database at `--db-path`
(never inside `--output-dir` — see [Configuration](#configuration)):

- **`runs`** — one row per pipeline run: `run_id`, `created_at` timestamp, and `article_count` (total articles considered that run — the denominator for comparing tag weights across runs, since a raw tag count means something different on a 400-article day than a 50-article one).
- **`urgency_scores`** — one row per topic per run; used by the z-score baseline after 7 runs per topic.
- **`bullets`** — one row per strategic bullet per topic per run; used by the bullet diff to identify what changed since the most recent prior run.

Every row carries its own `created_at` timestamp in addition to `run_id`, and
nothing is pruned — unlike the JSON files this replaced, which capped bullet
history at the last 7 runs, the database keeps full history indefinitely.

Weekend runs will produce thinner output — most news sources don't publish on weekends.

---

## License

[MIT](LICENSE)
