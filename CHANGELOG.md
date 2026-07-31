# Changelog

All notable changes to this project are documented here. Entries are
grouped by date rather than a semantic version number, since this project
doesn't tag releases.

## Unreleased (`sqlite3-urgency-and-bullets` branch)

### Added
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
