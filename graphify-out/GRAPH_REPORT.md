# Graph Report - .  (2026-07-31)

## Corpus Check
- Corpus is ~49,086 words - fits in a single context window. You may not need a graph.

## Summary
- 769 nodes · 2136 edges · 47 communities (41 shown, 6 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 202 edges (avg confidence: 0.5)
- Token cost: 86,896 input · 0 output

## Community Hubs (Navigation)
- Article Archiving
- Structured LLM Output Models
- Urgency Scoring & Alerting
- Design Rationale (AGENTS.md)
- HTML Rendering Tests
- Tag Normalization & Archive Query
- Tag Graph Bridge/Community Tests
- Prefect Daily Flow
- Async Pipeline Orchestration
- Strategic Insight Model
- RSS Feed Fetching
- Bullet Diff History
- Emerging Tag Rate Tracking
- LLM Client (litellm/instructor)
- CLI Entrypoint
- DB Connection & Community Summaries
- Run Registration & Bridge Tags
- Tag Tracking Persistence
- RSS Ingestion Module
- Core Data Models
- Article Summary Validation
- Test Fixtures
- Prompt Builder Tests
- Summarize Prompt Builder
- Token Usage Accounting
- Cross-Topic Overview Archive
- Strategy Prompt Builder
- System Message Smoke Tests
- Emerging Tag Alert Persistence
- Core Package Init & Archive Query
- Cross-Topic Synthesis & Rendering
- Tag Co-occurrence Graph Builder
- DB Path Safety Guard Tests
- Tracing (Langfuse/Phoenix)
- Bullet Diff Classification
- Packaged Data Paths
- Output-Dir Wipe Rationale
- Git Identity Convention
- core/models.py
- core/prompts.py
- core/tag_normalizer.py
- Project Identity (strategic-reports)

## God Nodes (most connected - your core abstractions)
1. `TopicResult` - 108 edges
2. `TopicConfig` - 81 edges
3. `ArticleSummary` - 75 edges
4. `record_run()` - 64 edges
5. `connect()` - 54 edges
6. `StrategicInsight` - 54 edges
7. `TokenUsage` - 44 edges
8. `LLMClient` - 35 edges
9. `RawArticle` - 35 edges
10. `run()` - 34 edges

## Surprising Connections (you probably didn't know these)
- `TestAnswerArchiveQuestion` --uses--> `LLMClient`  [INFERRED]
  tests/test_pipeline.py → strategic_reports/daily/core/llm_client.py
- `TestExtractQueryTags` --uses--> `LLMClient`  [INFERRED]
  tests/test_pipeline.py → strategic_reports/daily/core/llm_client.py
- `TestRunPipeline` --uses--> `LLMClient`  [INFERRED]
  tests/test_pipeline.py → strategic_reports/daily/core/llm_client.py
- `TestSummarizeCommunities` --uses--> `LLMClient`  [INFERRED]
  tests/test_pipeline.py → strategic_reports/daily/core/llm_client.py
- `TestFetchOneFeed` --uses--> `FeedConfig`  [INFERRED]
  tests/test_ingestion.py → strategic_reports/daily/core/models.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Tracking-db modules: db_path-only access, own short-lived connection per call** — core_db_module, core_urgency_module, core_bullet_diff_module, core_article_archive_module, core_overview_archive_module, core_tag_tracking_module, core_archive_query_module, core_rdf_export_module [EXTRACTED 1.00]
- **cli.py's four commands (run/ask/export-rdf)** — daily_cli_module, daily_cli_run_command, daily_cli_ask_command, daily_cli_export_rdf_command [EXTRACTED 1.00]
- **`ask` graph-guided retrieval flow over Louvain tag-communities** — daily_cli_ask_command, core_archive_query_module, core_pipeline_module, core_tag_graph_module [EXTRACTED 1.00]

## Communities (47 total, 6 thin omitted)

### Community 0 - "Article Archiving"
Cohesion: 0.06
Nodes (48): Graph, load_articles(), Path, Persists each run's article summaries into the tracking database, linked to run_, Insert this run's article summaries (title, link, publish_date, summary     bull, Reconstruct this run's article summaries from the database: a list of     dicts, record_articles(), The complete output for one topic after the pipeline finishes.      This is the (+40 more)

### Community 1 - "Structured LLM Output Models"
Cohesion: 0.07
Nodes (39): BaseModel, ArchiveAnswer, ArticleSummaryBatch, CommunitySummary, QueryTags, Wrapper for a batch of ArticleSummary objects.      instructor needs a top-level, LLM-generated summary of one Louvain tag-community's news coverage.      Grounde, LLM-extracted candidate tags for a free-text archive question.      Used for gra (+31 more)

### Community 2 - "Urgency Scoring & Alerting"
Cohesion: 0.10
Nodes (29): append_run(), check_alerts(), load_history(), Path, Urgency scoring history and alert detection — SQLite-backed.  Each pipeline run, Return alerts for topics whose urgency score is anomalously high.      For each, Return every topic's historical urgency scores, oldest-first per topic.      Doe, Insert the current run's urgency scores into the database.      Assumes db.recor (+21 more)

### Community 3 - "Design Rationale (AGENTS.md)"
Cohesion: 0.07
Nodes (42): `ask` is graph-guided retrieval, not full GraphRAG, Bridge tags ground cross-topic synthesis in graph structure, CLI/Prefect-flow feature parity convention, Community summaries grounded in member articles, not label alone, --db-path must never resolve inside --output-dir, Per-topic error isolation, `export-rdf` complements tag_graph.py, doesn't replace it, Tag rates (not raw counts) compared across runs (+34 more)

### Community 4 - "HTML Rendering Tests"
Cohesion: 0.06
Nodes (18): Path, Render all pipeline results to HTML files in output_dir.      This is a pure ren, render_report(), All strategy bullet points must appear in the index page., The index page should link to the per-topic summaries page.         slug "feeds_, Error topics must show the error message (not crash silently)., Empty topics must show a 'no articles' message (not crash silently)., Token usage (1200) should appear somewhere on the index page. (+10 more)

### Community 5 - "Tag Normalization & Archive Query"
Cohesion: 0.11
Nodes (15): field_validator, find_relevant_communities(), Path, Find community_summaries rows relevant to candidate_tags, across every     run i, normalize_tag(), normalize_tags(), Tag normalization for article summary tags.  Normalization runs in three passes:, Normalize a single tag to its canonical form. (+7 more)

### Community 6 - "Tag Graph Bridge/Community Tests"
Cohesion: 0.17
Nodes (11): find_bridge_tags(), group_articles_by_community(), Group this run's articles by Louvain community (display_data's     node "communi, Return tags that appear across many different topics — structural     evidence o, _article(), _node(), Tests for strategic_reports.daily.core.tag_graph: find_bridge_tags() and group_a, The same article (by link) appearing via two tags in one community counts once. (+3 more)

### Community 7 - "Prefect Daily Flow"
Cohesion: 0.17
Nodes (24): flow, archive_articles(), build_tag_graph(), build_topic_configs(), check_emerging_tag_alerts(), daily_report_flow(), Path, Prefect flow for the daily strategic report pipeline.  This flow is configured f (+16 more)

### Community 8 - "Async Pipeline Orchestration"
Cohesion: 0.11
Nodes (23): _chunk(), extract_query_tags(), _process_topic(), Exception, Semaphore, Async pipeline orchestrator.  This module wires together ingestion, LLM processi, Summarize and tag articles in sequential batches.      Batches are sequential (n, Derive 3-5 strategic bullet points from the topic's article summaries.      This (+15 more)

### Community 9 - "Strategic Insight Model"
Cohesion: 0.11
Nodes (16): StrategicInsight, Tests for StrategicInsight.bullets (3-5 bullets)., 3 bullets — at the minimum., 5 bullets — at the maximum., 2 bullets — below min_length=3, should be rejected., 6 bullets — above max_length=5, should be rejected., TestStrategicInsight, Tests for HTML rendering (renderer.py) — covers all TopicResult states and XSS. (+8 more)

### Community 10 - "RSS Feed Fetching"
Cohesion: 0.16
Nodes (15): _fetch_one_feed(), Fetch one RSS feed and return articles published within hours_cutoff.      This, make_feed_entry(), make_parsed_feed(), Return a MagicMock that mimics a feedparser entry dict-like object.      feedpar, Return a MagicMock that mimics a feedparser parsed feed result.      feedparser., Feed with one recent and one old article — only the recent one is returned., If feedparser.parse raises (network error, timeout, etc.), _fetch_one_feed (+7 more)

### Community 11 - "Bullet Diff History"
Cohesion: 0.21
Nodes (12): append_bullet_run(), load_bullet_history(), Path, Historical bullet diffing between consecutive pipeline runs — SQLite-backed.  St, Return the most recent prior run's bullets, keyed by topic — this is     "yester, Insert today's strategic bullets into the database.      Assumes db.record_run(d, _make_config(), _make_result() (+4 more)

### Community 12 - "Emerging Tag Rate Tracking"
Cohesion: 0.21
Nodes (10): check_emerging_tags(), load_tag_rate_history(), Return every tag's historical rate (tag count / that run's total     article_cou, Flag tags whose rate this run is anomalously high relative to that     tag's own, Seed db_path with one run per rate, using article_count=100 so rate == count/100, Fewer than 7 historical runs — skipped regardless of how anomalous the current r, A tag with zero history is skipped, not flagged, no matter its current rate., All historical rates identical (std=0) — no absolute fallback for tags, so no al (+2 more)

### Community 13 - "LLM Client (litellm/instructor)"
Cohesion: 0.14
Nodes (13): Mode, retry, LLMClient, Read-only view of cumulative token usage across all calls., Extract token counts from a litellm completion response.          The bare try/e, Call the LLM and parse the response into a Pydantic model via instructor., Call the LLM and return the raw text response (no Pydantic parsing).          Us, Async, provider-agnostic LLM client built on litellm + instructor.      Pass any (+5 more)

### Community 14 - "CLI Entrypoint"
Cohesion: 0.20
Nodes (15): command, ask(), _build_topic_configs(), export_rdf_command(), Path, Daily strategic report pipeline — CLI entrypoint.  Two commands (naming one expl, Run the daily strategic report pipeline and write results to output_dir., Ask a free-text question about the accumulated strategic-reports archive.      G (+7 more)

### Community 15 - "DB Connection & Community Summaries"
Cohesion: 0.21
Nodes (8): Connection, connect(), Open the tracking database, creating it (and any missing parent     directories), Persist this run's LLM-written community summaries     (pipeline.summarize_commu, record_community_summaries(), Tests for strategic_reports.daily.core.db: the output_dir/db_path safety guard,, TestConnect, TestRecordCommunitySummaries

### Community 16 - "Run Registration & Bridge Tags"
Cohesion: 0.19
Nodes (9): Path, Register a pipeline run in the runs table, with the total number of     articles, record_run(), Persist the bridge tags surfaced to the cross-topic synthesis prompt     this ru, record_bridge_tags(), A second record_run() call for the same run_id is a no-op (INSERT OR IGNORE)., TestRecordRun, record_bridge_tags must work even when tag_topics has no rows for         this r (+1 more)

### Community 17 - "Tag Tracking Persistence"
Cohesion: 0.19
Nodes (11): Path, Per-run tag tracking and emerging-tag z-score alerting — SQLite-backed.  record_, Reconstruct tag_graph.json's {"nodes": [...], "links": [...]} shape     from the, Insert this run's tag graph into the tracking database, linked to     run_id: pe, rebuild_graph_data(), record_tags(), _make_article(), _make_results() (+3 more)

### Community 18 - "RSS Ingestion Module"
Cohesion: 0.17
Nodes (10): fetch_topic_articles(), Async RSS ingestion layer.  Replaces content_fetch_inator.py + pickle-file hand-, Fetch all RSS feeds for a topic concurrently and return deduplicated     article, Tests for RSS ingestion (ingestion.py) — feedparser is mocked via patch().  WHY, Tests for fetch_topic_articles() — the public multi-feed entry point., If the same URL appears in multiple feeds (syndication is common),         fetch, Articles from multiple feeds should be sorted newest-first in the output., If every feed raises an exception, fetch_topic_articles should return [] (+2 more)

### Community 19 - "Core Data Models"
Cohesion: 0.21
Nodes (12): FeedConfig, Data models for the strategic reports pipeline.  Every object that flows through, One RSS feed source, as stored in the feeds_*.json files., An article retrieved from an RSS feed, before any LLM processing.      publish_d, RawArticle, Tests for Pydantic data models (models.py).  WHAT WE'RE TESTING ----------------, Tests for TopicResult defaults and state variants.      TopicResult has three va, Basic smoke test for FeedConfig — it's a simple model with no constraints. (+4 more)

### Community 20 - "Article Summary Validation"
Cohesion: 0.17
Nodes (10): ArticleSummary, LLM-generated summary and tags for one article.      The Field() constraints (mi, 21 tags — max_length=20 should reject this., 20 tags — exactly at the max, should be accepted., Tests for ArticleSummary.summary (exactly 3 bullets) and .tags (5-20)., Happy path: 3 bullets, 5 tags — both at their minimums., Only 1 bullet — min_length=3 should reject this.         This is the key validat, 4 bullets — max_length=3 should reject this.         Prevents the LLM from sneak (+2 more)

### Community 21 - "Test Fixtures"
Cohesion: 0.17
Nodes (15): db_path(), fixture, Path, Shared fixtures for the strategic-reports test suite.  WHAT IS conftest.py? ----, A valid ArticleSummary with exactly 3 summary bullets and 5 tags.     Satisfies, A valid StrategicInsight with 3 bullets (minimum allowed by Pydantic)., A TopicConfig that points to a real temporary feeds JSON file.      tmp_path is, A successful TopicResult (all three stages completed).      This fixture depends (+7 more)

### Community 22 - "Prompt Builder Tests"
Cohesion: 0.15
Nodes (13): articles(), fixture, Tests for prompt builder functions (prompts.py).  WHY TEST PROMPTS? ------------, Tests for build_summarize_prompt(articles) → str.      Key properties to verify:, Edge case: single-article batch works correctly., Tests for build_strategy_prompt(topic_title, summaries) → str.      The strategy, Two topics with a successful strategy, for cross-topic prompt testing., Three RawArticles with different publish times for prompt content testing. (+5 more)

### Community 23 - "Summarize Prompt Builder"
Cohesion: 0.14
Nodes (8): build_summarize_prompt(), Format a batch of articles into the user message for summarization.      Each ar, Every article title must appear in the prompt.         If a title is missing, th, The --- ARTICLE N --- delimiters must be present so the model knows         wher, URLs must appear so the model can echo them back as the 'link' field         in, Dates must appear in the prompt. isoformat() should produce "2026-06-27...", The article count ("3") must appear so the model knows how many to process., Edge case: empty list produces a prompt with "0" in it (not a crash).

### Community 24 - "Token Usage Accounting"
Cohesion: 0.21
Nodes (8): Accumulated token counts from one or more LLM calls.      Making this a Pydantic, TokenUsage, Tests for TokenUsage addition (the __add__ method).      TokenUsage.__add__ is c, A new TokenUsage starts at all zeros., Basic + operator: fields are summed independently., Adding a zeroed TokenUsage is the identity operation., Simulates how the pipeline accumulates usage across multiple LLM calls., TestTokenUsage

### Community 25 - "Cross-Topic Overview Archive"
Cohesion: 0.27
Nodes (7): Path, Persists each run's cross-topic synthesis overview into the tracking database, l, Insert this run's cross-topic synthesis bullets into the database., record_overview(), _bullets_for_run(), Tests for strategic_reports.daily.core.overview_archive.  Covers:   - record_ove, TestRecordOverview

### Community 26 - "Strategy Prompt Builder"
Cohesion: 0.17
Nodes (7): build_strategy_prompt(), Format article summaries into the user message for strategic synthesis.      Thi, The topic title must appear so the model frames insights in the right domain., Article titles from the summaries must appear as section headers., The bullet points from each ArticleSummary must appear in the strategy prompt., Tags must appear so the model can identify thematic clusters.         A strategy, The count of summaries must appear in the prompt.

### Community 27 - "System Message Smoke Tests"
Cohesion: 0.17
Nodes (6): The strategist system prompt should reference strategy/strategic., The cross-topic system prompt should explain how to weigh bridge tags., Smoke tests for the system message constants.      We don't test the exact wordi, System message must be a meaningful string, not empty or whitespace., The summarizer system prompt should reference tagging., TestSystemMessages

### Community 28 - "Emerging Tag Alert Persistence"
Cohesion: 0.33
Nodes (5): EmergingTagAlert, Persist the emerging-tag alerts that fired this run, as an audit trail —     "wh, record_emerging_tag_alerts(), TestEmergingTagAlertSummary, TestRecordEmergingTagAlerts

### Community 29 - "Core Package Init & Archive Query"
Cohesion: 0.24
Nodes (4): Graph-guided retrieval over the accumulated archive.  find_relevant_communities(, SQLite tracking database — schema and connection helper shared by the CLI and th, Package init for strategic_reports.daily.core.  configure_logging() sets up stru, Async, provider-agnostic LLM client.  Key libraries:   litellm    — translates a

### Community 30 - "Cross-Topic Synthesis & Rendering"
Cohesion: 0.22
Nodes (8): Environment, CrossTopicSynthesis, LLM-generated strategic overview that synthesizes insights across all topics., Make a single LLM call to synthesize cross-cutting themes across all topics., synthesize_cross_topic(), _env(), HTML rendering layer — converts pipeline results into HTML files.  Uses Jinja2 t, Create a configured Jinja2 Environment.      FileSystemLoader tells Jinja2 where

### Community 31 - "Tag Co-occurrence Graph Builder"
Cohesion: 0.28
Nodes (8): build_display_graph(), build_graph_data(), Path, Tag co-occurrence network graph generator.  Two outputs:   tag_graph.json, Build the full node/edge graph from tag co-occurrence across all articles., Write tag_graph.json (full), tag_graph_display.json (pruned+communities),     an, Prune the full graph and annotate nodes with Louvain community IDs.      Steps:, write_tag_graph()

### Community 32 - "DB Path Safety Guard Tests"
Cohesion: 0.39
Nodes (3): ensure_safe_db_path(), Raise ValueError if db_path sits inside output_dir.      output_dir is deleted a, TestEnsureSafeDbPath

### Community 33 - "Tracing (Langfuse/Phoenix)"
Cohesion: 0.32
Nodes (7): Optional LLM observability integrations — both are opt-in via environment variab, Start a local Phoenix server and auto-instrument litellm via OpenTelemetry., Configure all available tracing backends.      Returns a dict showing which back, Enable litellm's Langfuse callback if credentials are present.      HOW LITELLM, setup_langfuse(), setup_phoenix(), setup_tracing()

### Community 34 - "Bullet Diff Classification"
Cohesion: 0.47
Nodes (6): diff_all_topics(), _diff_one_topic(), Semaphore, Diff today's bullets against yesterday's for every topic that has both.      Top, BulletDiff, LLM-classified diff between today's and yesterday's strategic bullets for one to

### Community 35 - "Packaged Data Paths"
Cohesion: 0.40
Nodes (4): default_data_dir(), Path, Packaged-data path helpers.  RSS feed configs ship as package data (see pyprojec, Path to the bundled default RSS feed configs (data/rss_feeds/*.json).

## Knowledge Gaps
- **9 isolated node(s):** `strategic-reports`, `paths.py (default_data_dir)`, `core/ingestion.py`, `core/prompts.py`, `core/renderer.py` (+4 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **6 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `TopicResult` connect `Article Archiving` to `Structured LLM Output Models`, `Urgency Scoring & Alerting`, `HTML Rendering Tests`, `Tag Graph Bridge/Community Tests`, `Prefect Daily Flow`, `Async Pipeline Orchestration`, `Strategic Insight Model`, `Bullet Diff History`, `Emerging Tag Rate Tracking`, `LLM Client (litellm/instructor)`, `DB Connection & Community Summaries`, `Run Registration & Bridge Tags`, `Tag Tracking Persistence`, `Core Data Models`, `Article Summary Validation`, `Test Fixtures`, `Prompt Builder Tests`, `Token Usage Accounting`, `System Message Smoke Tests`, `Emerging Tag Alert Persistence`, `Core Package Init & Archive Query`, `Cross-Topic Synthesis & Rendering`, `Tag Co-occurrence Graph Builder`, `Bullet Diff Classification`?**
  _High betweenness centrality (0.155) - this node is a cross-community bridge._
- **Why does `TopicConfig` connect `Article Archiving` to `Structured LLM Output Models`, `Urgency Scoring & Alerting`, `Tag Graph Bridge/Community Tests`, `Prefect Daily Flow`, `Async Pipeline Orchestration`, `Strategic Insight Model`, `RSS Feed Fetching`, `Bullet Diff History`, `Emerging Tag Rate Tracking`, `LLM Client (litellm/instructor)`, `CLI Entrypoint`, `DB Connection & Community Summaries`, `Run Registration & Bridge Tags`, `Tag Tracking Persistence`, `RSS Ingestion Module`, `Core Data Models`, `Test Fixtures`, `Prompt Builder Tests`, `System Message Smoke Tests`, `Emerging Tag Alert Persistence`, `Core Package Init & Archive Query`?**
  _High betweenness centrality (0.113) - this node is a cross-community bridge._
- **Why does `ArticleSummary` connect `Article Summary Validation` to `Article Archiving`, `Structured LLM Output Models`, `Tag Normalization & Archive Query`, `Tag Graph Bridge/Community Tests`, `Async Pipeline Orchestration`, `Strategic Insight Model`, `Emerging Tag Rate Tracking`, `DB Connection & Community Summaries`, `Run Registration & Bridge Tags`, `Tag Tracking Persistence`, `Core Data Models`, `Test Fixtures`, `Prompt Builder Tests`, `Token Usage Accounting`, `Strategy Prompt Builder`, `System Message Smoke Tests`, `Emerging Tag Alert Persistence`, `Core Package Init & Archive Query`, `Tag Co-occurrence Graph Builder`?**
  _High betweenness centrality (0.084) - this node is a cross-community bridge._
- **Are the 39 inferred relationships involving `TopicResult` (e.g. with `UrgencyAlert` and `TestRecordAndLoadArticles`) actually correct?**
  _`TopicResult` has 39 INFERRED edges - model-reasoned connections that need verification._
- **Are the 34 inferred relationships involving `TopicConfig` (e.g. with `TestRecordAndLoadArticles` and `TestLoadBulletHistory`) actually correct?**
  _`TopicConfig` has 34 INFERRED edges - model-reasoned connections that need verification._
- **Are the 32 inferred relationships involving `ArticleSummary` (e.g. with `TestRecordAndLoadArticles` and `TestArticleSummary`) actually correct?**
  _`ArticleSummary` has 32 INFERRED edges - model-reasoned connections that need verification._
- **What connects `strategic-reports`, `paths.py (default_data_dir)`, `core/ingestion.py` to the rest of the system?**
  _9 weakly-connected nodes found - possible documentation gaps or missing edges._