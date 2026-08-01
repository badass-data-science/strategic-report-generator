# Graph Report - .  (2026-08-01)

## Corpus Check
- 10 files · ~86,555 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 804 nodes · 2055 edges · 50 communities (44 shown, 6 thin omitted)
- Extraction: 90% EXTRACTED · 10% INFERRED · 0% AMBIGUOUS · INFERRED: 210 edges (avg confidence: 0.53)
- Token cost: 128,368 input · 0 output

## Community Hubs (Navigation)
- Article Archiving
- Data Models & Validation
- Urgency Scoring & Alerting
- Graph/RDF Design Rationale
- CLI Entrypoint
- RDF Export (rdf_export.py)
- Tag Normalization & Archive Query
- Prefect Daily Flow
- Async Pipeline Orchestration Tests
- Tag Graph Bridge/Community Tests
- Structured LLM Output Models
- DB Connection & Community Summaries
- RSS Feed Fetching Mocks
- Tag Co-occurrence Graph Builder
- Async Pipeline Orchestration
- Bullet Diff & Ingestion
- LLM Client (litellm/instructor)
- Archive Query & Overview Persistence
- Summarize Prompt Builder Tests
- Emerging Tag Rate Tracking
- Index Page Rendering Tests
- Topic Summary Page Tests
- Strategy Prompt Builder Tests
- Emerging Tag Alert Persistence
- Renderer Test Fixtures
- RSS Ingestion Tests
- Community Summarization Tests
- System Message Smoke Tests
- Prompt Builder Test Fixtures
- Cross-Topic Prompt Builder Tests
- Cross-Topic Synthesis & Rendering
- Article JSON-LD Markup Tests
- Bridge Tag Persistence Tests
- Tag Network Graph Screenshot
- Jinja2 Rendering Environment
- Companion Blog Posts
- export-rdf command (isolated)
- flow (isolated)
- CI Workflow (isolated)
- Project Identity (isolated)
- task (isolated)
- fixture (isolated)

## God Nodes (most connected - your core abstractions)
1. `TopicResult` - 87 edges
2. `TopicConfig` - 71 edges
3. `ArticleSummary` - 70 edges
4. `record_run()` - 62 edges
5. `connect()` - 53 edges
6. `StrategicInsight` - 49 edges
7. `TokenUsage` - 38 edges
8. `RawArticle` - 35 edges
9. `run()` - 34 edges
10. `render_report()` - 34 edges

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
- **Graph-guided archive retrieval flow (ask command)** — daily_cli_ask, core_pipeline_extract_query_tags, core_archive_query_find_relevant_communities, core_pipeline_answer_archive_question [EXTRACTED 1.00]
- **RDF export's reused-vocabulary ontology (SKOS + PROV-O + schema.org + stratrep:)** — core_rdf_export_module, blog_posts_strategic_intelligence_knowledge_graph_skos, blog_posts_strategic_intelligence_knowledge_graph_prov_o, blog_posts_strategic_intelligence_knowledge_graph_schema_org, blog_posts_strategic_intelligence_knowledge_graph_stratrep_namespace [EXTRACTED 1.00]
- **Structural/statistical signals that ground the pipeline without LLM invention** — core_tag_graph_find_bridge_tags, core_tag_tracking_check_emerging_tags, core_urgency_check_alerts [INFERRED 0.85]
- **Tag Network Graph renders four dominant topic communities via Louvain clustering** — blog_posts_tag_network_graph, blog_posts_louvain_community_detection, blog_posts_ai_technology_cluster, blog_posts_cryptocurrency_cluster, blog_posts_finance_business_cluster [INFERRED 0.75]

## Communities (50 total, 6 thin omitted)

### Community 0 - "Article Archiving"
Cohesion: 0.08
Nodes (41): load_articles(), Path, Persists each run's article summaries into the tracking database, linked to run_, Insert this run's article summaries (title, link, publish_date, summary     bull, Reconstruct this run's article summaries from the database: a list of     dicts, record_articles(), append_bullet_run(), load_bullet_history() (+33 more)

### Community 1 - "Data Models & Validation"
Cohesion: 0.05
Nodes (32): FeedConfig, One RSS feed source, as stored in the feeds_*.json files., StrategicInsight, Tests for Pydantic data models (models.py).  WHAT WE'RE TESTING ----------------, 21 tags — max_length=20 should reject this., 20 tags — exactly at the max, should be accepted., Tests for StrategicInsight.bullets (3-5 bullets)., 3 bullets — at the minimum. (+24 more)

### Community 2 - "Urgency Scoring & Alerting"
Cohesion: 0.10
Nodes (27): append_run(), check_alerts(), load_history(), Path, Urgency scoring history and alert detection — SQLite-backed.  Each pipeline run, Return alerts for topics whose urgency score is anomalously high.      For each, Return every topic's historical urgency scores, oldest-first per topic.      Doe, Insert the current run's urgency scores into the database.      Assumes db.recor (+19 more)

### Community 3 - "Graph/RDF Design Rationale"
Cohesion: 0.05
Nodes (38): Bridge Tags, LLM-Written Community Summaries, Cypher query language, GQL (ISO Graph Query Language, ratified 2024), Graph-Guided Retrieval (the ask command), Full GraphRAG (contrasted concept), Louvain Community Detection, Neo4j (+30 more)

### Community 4 - "CLI Entrypoint"
Cohesion: 0.08
Nodes (34): command, ask(), _build_topic_configs(), export_rdf_command(), Path, Daily strategic report pipeline — CLI entrypoint.  Two commands (naming one expl, Run the daily strategic report pipeline and write results to output_dir., Ask a free-text question about the accumulated strategic-reports archive.      G (+26 more)

### Community 5 - "RDF Export (rdf_export.py)"
Cohesion: 0.09
Nodes (27): Graph, build_graph(), export_rdf(), _placeholders(), Path, Builds an RDF (Turtle) export of the tracking database's accumulated knowledge —, Build the RDF graph from the database and serialize it to output_path     as Tur, Turn arbitrary text into a URI-safe path segment. (+19 more)

### Community 6 - "Tag Normalization & Archive Query"
Cohesion: 0.11
Nodes (15): field_validator, find_relevant_communities(), Path, Find community_summaries rows relevant to candidate_tags, across every     run i, normalize_tag(), normalize_tags(), Tag normalization for article summary tags.  Normalization runs in three passes:, Normalize a single tag to its canonical form. (+7 more)

### Community 7 - "Prefect Daily Flow"
Cohesion: 0.15
Nodes (29): archive_articles(), build_tag_graph(), build_topic_configs(), check_emerging_tag_alerts(), check_urgency_alerts(), daily_report_flow(), BulletDiff, CrossTopicSynthesis (+21 more)

### Community 8 - "Async Pipeline Orchestration Tests"
Cohesion: 0.13
Nodes (18): Run the full pipeline for all topics and return one TopicResult per topic., run_pipeline(), make_articles(), make_mock_client(), Exception, Build n RawArticles for use as mock ingestion output., Tests for run_pipeline() — the public pipeline entry point., run_pipeline must return exactly one TopicResult per input topic. (+10 more)

### Community 9 - "Tag Graph Bridge/Community Tests"
Cohesion: 0.17
Nodes (11): find_bridge_tags(), group_articles_by_community(), Group this run's articles by Louvain community (display_data's     node "communi, Return tags that appear across many different topics — structural     evidence o, _article(), _node(), Tests for strategic_reports.daily.core.tag_graph: find_bridge_tags() and group_a, The same article (by link) appearing via two tags in one community counts once. (+3 more)

### Community 10 - "Structured LLM Output Models"
Cohesion: 0.15
Nodes (19): BaseModel, ArchiveAnswer, ArticleSummaryBatch, CommunitySummary, QueryTags, Wrapper for a batch of ArticleSummary objects.      instructor needs a top-level, LLM-generated summary of one Louvain tag-community's news coverage.      Grounde, LLM-extracted candidate tags for a free-text archive question.      Used for gra (+11 more)

### Community 11 - "DB Connection & Community Summaries"
Cohesion: 0.16
Nodes (13): Connection, connect(), Path, Open the tracking database, creating it (and any missing parent     directories), Register a pipeline run in the runs table, with the total number of     articles, record_run(), Persist this run's LLM-written community summaries     (pipeline.summarize_commu, record_community_summaries() (+5 more)

### Community 12 - "RSS Feed Fetching Mocks"
Cohesion: 0.15
Nodes (16): _fetch_one_feed(), Fetch one RSS feed and return articles published within hours_cutoff.      This, make_feed_entry(), make_parsed_feed(), Return a MagicMock that mimics a feedparser entry dict-like object.      feedpar, Return a MagicMock that mimics a feedparser parsed feed result.      feedparser., Tests for RSS ingestion (ingestion.py) — feedparser is mocked via patch().  WHY, Feed with one recent and one old article — only the recent one is returned. (+8 more)

### Community 13 - "Tag Co-occurrence Graph Builder"
Cohesion: 0.16
Nodes (15): ArticleSummary, LLM-generated summary and tags for one article.      The Field() constraints (mi, build_graph_data(), Tag co-occurrence network graph generator.  Two outputs:   tag_graph.json, Build the full node/edge graph from tag co-occurrence across all articles., Path, Reconstruct tag_graph.json's {"nodes": [...], "links": [...]} shape     from the, Insert this run's tag graph into the tracking database, linked to     run_id: pe (+7 more)

### Community 14 - "Async Pipeline Orchestration"
Cohesion: 0.12
Nodes (20): _chunk(), extract_query_tags(), _process_topic(), Exception, Semaphore, Async pipeline orchestrator.  This module wires together ingestion, LLM processi, Summarize and tag articles in sequential batches.      Batches are sequential (n, Derive 3-5 strategic bullet points from the topic's article summaries.      This (+12 more)

### Community 15 - "Bullet Diff & Ingestion"
Cohesion: 0.18
Nodes (15): diff_all_topics(), _diff_one_topic(), Semaphore, Historical bullet diffing between consecutive pipeline runs — SQLite-backed.  St, Diff today's bullets against yesterday's for every topic that has both.      Top, Async RSS ingestion layer.  Replaces content_fetch_inator.py + pickle-file hand-, Package init for strategic_reports.daily.core.  configure_logging() sets up stru, BulletDiff (+7 more)

### Community 16 - "LLM Client (litellm/instructor)"
Cohesion: 0.16
Nodes (12): Mode, retry, LLMClient, Async, provider-agnostic LLM client.  Key libraries:   litellm    — translates a, Read-only view of cumulative token usage across all calls., Extract token counts from a litellm completion response.          The bare try/e, Call the LLM and parse the response into a Pydantic model via instructor., Call the LLM and return the raw text response (no Pydantic parsing).          Us (+4 more)

### Community 17 - "Archive Query & Overview Persistence"
Cohesion: 0.17
Nodes (9): Graph-guided retrieval over the accumulated archive.  find_relevant_communities(, SQLite tracking database — schema and connection helper shared by the CLI and th, Path, Persists each run's cross-topic synthesis overview into the tracking database, l, Insert this run's cross-topic synthesis bullets into the database., record_overview(), _bullets_for_run(), Tests for strategic_reports.daily.core.overview_archive.  Covers:   - record_ove (+1 more)

### Community 18 - "Summarize Prompt Builder Tests"
Cohesion: 0.15
Nodes (11): build_summarize_prompt(), Format a batch of articles into the user message for summarization.      Each ar, Tests for build_summarize_prompt(articles) → str.      Key properties to verify:, Every article title must appear in the prompt.         If a title is missing, th, The --- ARTICLE N --- delimiters must be present so the model knows         wher, URLs must appear so the model can echo them back as the 'link' field         in, Dates must appear in the prompt. isoformat() should produce "2026-06-27...", The article count ("3") must appear so the model knows how many to process. (+3 more)

### Community 19 - "Emerging Tag Rate Tracking"
Cohesion: 0.23
Nodes (10): check_emerging_tags(), load_tag_rate_history(), Return every tag's historical rate (tag count / that run's total     article_cou, Flag tags whose rate this run is anomalously high relative to that     tag's own, Seed db_path with one run per rate, using article_count=100 so rate == count/100, Fewer than 7 historical runs — skipped regardless of how anomalous the current r, A tag with zero history is skipped, not flagged, no matter its current rate., All historical rates identical (std=0) — no absolute fallback for tags, so no al (+2 more)

### Community 20 - "Index Page Rendering Tests"
Cohesion: 0.12
Nodes (9): The topic title must appear so the reader can identify each section., The index page should link to the per-topic summaries page.         slug "feeds_, Error topics must show the error message (not crash silently)., Empty topics must show a 'no articles' message (not crash silently)., Token usage (1200) should appear somewhere on the index page., Renderer must handle a mixed list of result states in one call.         All thre, Tests for index.html — the main strategic report page., index.html must always be created, regardless of topic states. (+1 more)

### Community 21 - "Topic Summary Page Tests"
Cohesion: 0.12
Nodes (9): Tests for {topic}_summaries.html pages — per-topic article detail pages., feeds_ai slug → ai_summaries.html (after removeprefix("feeds_"))., Topics with no articles should NOT produce a summaries file.         There's not, Topics with errors also don't get a summaries file., The article title from the fixture must appear in the summaries page., The article URL must appear as a link in the summaries page., At least one tag from the fixture should appear in the summaries page., The summaries page should have a link back to index.html for navigation. (+1 more)

### Community 22 - "Strategy Prompt Builder Tests"
Cohesion: 0.19
Nodes (9): build_strategy_prompt(), Format article summaries into the user message for strategic synthesis.      Thi, Tests for build_strategy_prompt(topic_title, summaries) → str.      The strategy, The topic title must appear so the model frames insights in the right domain., Article titles from the summaries must appear as section headers., The bullet points from each ArticleSummary must appear in the strategy prompt., Tags must appear so the model can identify thematic clusters.         A strategy, The count of summaries must appear in the prompt. (+1 more)

### Community 23 - "Emerging Tag Alert Persistence"
Cohesion: 0.27
Nodes (6): EmergingTagAlert, Per-run tag tracking and emerging-tag z-score alerting — SQLite-backed.  record_, Persist the emerging-tag alerts that fired this run, as an audit trail —     "wh, record_emerging_tag_alerts(), TestEmergingTagAlertSummary, TestRecordEmergingTagAlerts

### Community 24 - "Renderer Test Fixtures"
Cohesion: 0.21
Nodes (10): fixture, empty_result(), error_result(), Tests for HTML rendering (renderer.py) — covers all TopicResult states and XSS., render_report should create the output directory if it doesn't exist.         mk, A TopicResult with articles and a completed strategy — the 'success' state., A TopicResult where ingestion failed — the 'error' state., A TopicResult where no recent articles were found — the 'empty' state. (+2 more)

### Community 25 - "RSS Ingestion Tests"
Cohesion: 0.21
Nodes (8): fetch_topic_articles(), Fetch all RSS feeds for a topic concurrently and return deduplicated     article, Tests for fetch_topic_articles() — the public multi-feed entry point., If the same URL appears in multiple feeds (syndication is common),         fetch, Articles from multiple feeds should be sorted newest-first in the output., If every feed raises an exception, fetch_topic_articles should return [], fetch_topic_articles reads feed URLs from the feeds JSON file on disk.         T, TestFetchTopicArticles

### Community 26 - "Community Summarization Tests"
Cohesion: 0.39
Nodes (6): Generate an LLM-written paragraph summary for each Louvain community in     disp, summarize_communities(), _article(), make_community_mock_client(), A mock client that answers CommunitySummary requests, optionally     failing whe, TestSummarizeCommunities

### Community 27 - "System Message Smoke Tests"
Cohesion: 0.17
Nodes (6): The strategist system prompt should reference strategy/strategic., The cross-topic system prompt should explain how to weigh bridge tags., Smoke tests for the system message constants.      We don't test the exact wordi, System message must be a meaningful string, not empty or whitespace., The summarizer system prompt should reference tagging., TestSystemMessages

### Community 28 - "Prompt Builder Test Fixtures"
Cohesion: 0.24
Nodes (10): An article retrieved from an RSS feed, before any LLM processing.      publish_d, RawArticle, articles(), fixture, Tests for prompt builder functions (prompts.py).  WHY TEST PROMPTS? ------------, Two topics with a successful strategy, for cross-topic prompt testing., Three RawArticles with different publish times for prompt content testing., One ArticleSummary for testing the strategy prompt builder. (+2 more)

### Community 29 - "Cross-Topic Prompt Builder Tests"
Cohesion: 0.27
Nodes (5): build_cross_topic_prompt(), Format all per-topic strategic insights into the user message for cross-topic sy, Tests for build_cross_topic_prompt(results, bridge_tags) → str.      bridge_tags, An empty list (no qualifying bridge tags) should behave like None., TestBuildCrossTopicPrompt

### Community 30 - "Cross-Topic Synthesis & Rendering"
Cohesion: 0.20
Nodes (8): BulletDiff, CrossTopicSynthesis, Path, TopicResult, Render all pipeline results to HTML files in output_dir.      This is a pure ren, render_report(), All strategy bullet points must appear in the index page., SECURITY/STRUCTURED-DATA TEST: each article gets a schema:Article         JSON-L

### Community 31 - "Article JSON-LD Markup Tests"
Cohesion: 0.22
Nodes (6): ArticleSummary, _build_article_jsonld(), Build a JSON-LD array of schema:Article objects for a topic's articles —     the, SECURITY TEST: article titles from RSS feeds must be HTML-escaped.          Jinj, A malicious article title containing "</script>" must not be able to         bre, Verify the slug → filename mapping works for multi-word slugs.         feeds_dat

### Community 32 - "Bridge Tag Persistence Tests"
Cohesion: 0.33
Nodes (4): Persist the bridge tags surfaced to the cross-topic synthesis prompt     this ru, record_bridge_tags(), record_bridge_tags must work even when tag_topics has no rows for         this r, TestRecordBridgeTags

### Community 33 - "Tag Network Graph Screenshot"
Cohesion: 0.32
Nodes (8): AI/technology tag cluster (anthropic, openai, machine learning, cybersecurity, chatbot, model, security, government, policy, aerospace), Cryptocurrency/DeFi tag cluster (cryptocurrency, blockchain, defi, stablecoin, trading, market, token, ethereum), Finance/business tag cluster (fintech, hedge fund, asset management, insider trading, consumer goods, energy, mining, logistics, e-commerce, real estate investment trust, prediction market), Louvain community detection (node coloring algorithm), Military/defense tag cluster (weapons, army, defense, aviation, maritime, aerospace), Tag co-occurrence network (tags linked when co-occurring in same article), Tag Network Graph (interactive tag co-occurrence viewer), Tag Network Graph screenshot (415 tags, 2881 connections, 15 communities)

### Community 34 - "Jinja2 Rendering Environment"
Cohesion: 0.40
Nodes (4): Environment, _env(), HTML rendering layer — converts pipeline results into HTML files.  Uses Jinja2 t, Create a configured Jinja2 Environment.      FileSystemLoader tells Jinja2 where

### Community 35 - "Companion Blog Posts"
Cohesion: 0.67
Nodes (3): "Daily Strategic Intelligence, Automated" (earlier companion post), Strategic Intelligence, Interconnected (blog post), "The Data-Driven DJ" (earlier post, Neo4j/Cypher for DJ track transitions)

## Knowledge Gaps
- **23 isolated node(s):** `strategic-reports`, `Tests GitHub Actions Workflow`, `"Daily Strategic Intelligence, Automated" (earlier companion post)`, `"The Data-Driven DJ" (earlier post, Neo4j/Cypher for DJ track transitions)`, `Full GraphRAG (contrasted concept)` (+18 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **6 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `render_report()` connect `Cross-Topic Synthesis & Rendering` to `Jinja2 Rendering Environment`, `CLI Entrypoint`, `Prefect Daily Flow`, `Bullet Diff & Ingestion`, `Index Page Rendering Tests`, `Topic Summary Page Tests`, `Renderer Test Fixtures`, `Article JSON-LD Markup Tests`?**
  _High betweenness centrality (0.115) - this node is a cross-community bridge._
- **Why does `TopicResult` connect `Article Archiving` to `Data Models & Validation`, `Urgency Scoring & Alerting`, `CLI Entrypoint`, `RDF Export (rdf_export.py)`, `Async Pipeline Orchestration Tests`, `Tag Graph Bridge/Community Tests`, `Structured LLM Output Models`, `DB Connection & Community Summaries`, `Tag Co-occurrence Graph Builder`, `Async Pipeline Orchestration`, `Bullet Diff & Ingestion`, `Summarize Prompt Builder Tests`, `Emerging Tag Rate Tracking`, `Strategy Prompt Builder Tests`, `Emerging Tag Alert Persistence`, `Community Summarization Tests`, `System Message Smoke Tests`, `Prompt Builder Test Fixtures`, `Cross-Topic Prompt Builder Tests`, `Bridge Tag Persistence Tests`?**
  _High betweenness centrality (0.094) - this node is a cross-community bridge._
- **Why does `TopicConfig` connect `Article Archiving` to `Urgency Scoring & Alerting`, `CLI Entrypoint`, `RDF Export (rdf_export.py)`, `Async Pipeline Orchestration Tests`, `Tag Graph Bridge/Community Tests`, `Structured LLM Output Models`, `DB Connection & Community Summaries`, `RSS Feed Fetching Mocks`, `Tag Co-occurrence Graph Builder`, `Async Pipeline Orchestration`, `Bullet Diff & Ingestion`, `Summarize Prompt Builder Tests`, `Emerging Tag Rate Tracking`, `Strategy Prompt Builder Tests`, `Emerging Tag Alert Persistence`, `RSS Ingestion Tests`, `Community Summarization Tests`, `System Message Smoke Tests`, `Prompt Builder Test Fixtures`, `Cross-Topic Prompt Builder Tests`, `Bridge Tag Persistence Tests`?**
  _High betweenness centrality (0.091) - this node is a cross-community bridge._
- **Are the 37 inferred relationships involving `TopicResult` (e.g. with `UrgencyAlert` and `TestRecordAndLoadArticles`) actually correct?**
  _`TopicResult` has 37 INFERRED edges - model-reasoned connections that need verification._
- **Are the 32 inferred relationships involving `TopicConfig` (e.g. with `TestRecordAndLoadArticles` and `TestLoadBulletHistory`) actually correct?**
  _`TopicConfig` has 32 INFERRED edges - model-reasoned connections that need verification._
- **Are the 30 inferred relationships involving `ArticleSummary` (e.g. with `TestRecordAndLoadArticles` and `TestArticleSummary`) actually correct?**
  _`ArticleSummary` has 30 INFERRED edges - model-reasoned connections that need verification._
- **What connects `strategic-reports`, `Tests GitHub Actions Workflow`, `"Daily Strategic Intelligence, Automated" (earlier companion post)` to the rest of the system?**
  _23 weakly-connected nodes found - possible documentation gaps or missing edges._