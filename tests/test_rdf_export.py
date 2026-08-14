"""
Tests for strategic_reports.daily.core.rdf_export.

Covers:
  - articles map to schema:Article with tags (skos:Concept) and summary
    bullets
  - community summaries map to skos:Collection with skos:member tags
  - bridge tags map to BridgeTagObservation nodes
  - per-topic urgency scores and strategic bullets share one TopicRun node
  - cross-topic overview bullets are included
  - --since filters to runs at or after a given run_id/timestamp
  - export_rdf writes a parseable Turtle file
"""

from pathlib import Path

from rdflib import RDF, RDFS, XSD, Literal
from rdflib.namespace import PROV, SDO, SKOS
from strategic_reports.daily.core.article_archive import record_articles
from strategic_reports.daily.core.bullet_diff import append_bullet_run
from strategic_reports.daily.core.db import record_run
from strategic_reports.daily.core.models import (
    ArticleSummary,
    StrategicInsight,
    TopicConfig,
    TopicResult,
)
from strategic_reports.daily.core.overview_archive import record_overview
from strategic_reports.daily.core.rdf_export import BASE, STRATREP, build_graph, export_rdf
from strategic_reports.daily.core.tag_normalizer import normalize_tags
from strategic_reports.daily.core.tag_tracking import record_bridge_tags, record_community_summaries
from strategic_reports.daily.core.urgency import append_run

_TAGS = normalize_tags(["ai", "tech", "models", "research", "benchmarks"])


def _make_result(topic_title: str = "Artificial Intelligence") -> TopicResult:
    config = TopicConfig(slug="feeds_ai", title=topic_title, feeds_file=Path("/dev/null"))
    article = ArticleSummary(
        title="LLMs Keep Improving",
        link="https://example.com/llms",
        publish_date="2026-07-31T09:00:00",
        summary=["Models are faster.", "Costs are dropping.", "New benchmarks set."],
        tags=_TAGS,
    )
    strategy = StrategicInsight(
        bullets=[
            "Invest in LLM tooling now.",
            "Target applied-AI roles.",
            "Build small deployed projects.",
        ],
        urgency_score=0.42,
    )
    return TopicResult(config=config, articles=[article], strategy=strategy)


def _seed_full_run(database_url: str, run_id: str, article_count: int = 1) -> None:
    record_run(database_url, run_id, article_count=article_count)
    results = [_make_result()]
    record_articles(database_url, run_id, results)
    append_run(database_url, results, run_id)
    append_bullet_run(database_url, results, run_id)
    record_community_summaries(
        database_url,
        run_id,
        {0: {
            "label": "AI", "tags": _TAGS,
            "summary": "A cluster about AI progress.", "article_count": 1,
        }},
    )
    record_bridge_tags(
        database_url,
        run_id,
        [{"tag": _TAGS[0], "topics": ["Artificial Intelligence", "Business"], "count": 4}],
    )
    record_overview(database_url, run_id, [f"Overview bullet for {run_id}."])


class TestBuildGraphArticles:
    def test_article_has_headline_url_and_tags(self, database_url: str) -> None:
        _seed_full_run(database_url, "run-0")
        graph = build_graph(database_url)

        articles = list(graph.subjects(RDF.type, SDO.Article))
        assert len(articles) == 1
        article = articles[0]
        assert graph.value(article, SDO.headline) == Literal("LLMs Keep Improving")
        assert str(graph.value(article, SDO.url)) == "https://example.com/llms"

        tag_objects = list(graph.objects(article, STRATREP.hasTag))
        tag_labels = {str(graph.value(t, SKOS.prefLabel)) for t in tag_objects}
        assert tag_labels == set(_TAGS)

        summary_bullets = {str(b) for b in graph.objects(article, STRATREP.summaryBullet)}
        assert summary_bullets == {
            "Models are faster.", "Costs are dropping.", "New benchmarks set.",
        }

    def test_article_generated_by_its_run(self, database_url: str) -> None:
        _seed_full_run(database_url, "run-0")
        graph = build_graph(database_url)
        article = next(graph.subjects(RDF.type, SDO.Article))
        assert (article, PROV.wasGeneratedBy, BASE["run/run-0"]) in graph

    def test_date_published_is_typed_xsd_datetime(self, database_url: str) -> None:
        """Regression guard: `Literal(publish_date)` with no `datatype=` used to
        serialize as a bare, untyped string -- valid RDF, but importers (e.g.
        neosemantics) then had no way to tell it apart from ordinary text and
        loaded it as a Neo4j STRING instead of a native temporal type,
        breaking date comparisons/arithmetic downstream."""
        _seed_full_run(database_url, "run-0")
        graph = build_graph(database_url)
        article = next(graph.subjects(RDF.type, SDO.Article))
        date_literal = graph.value(article, SDO.datePublished)
        assert isinstance(date_literal, Literal)
        assert date_literal.datatype == XSD.dateTime
        assert str(date_literal) == "2026-07-31T09:00:00"

    def test_run_started_at_time_is_typed_xsd_datetime(self, database_url: str) -> None:
        _seed_full_run(database_url, "run-0")
        graph = build_graph(database_url)
        started_at = graph.value(BASE["run/run-0"], PROV.startedAtTime)
        assert isinstance(started_at, Literal)
        assert started_at.datatype == XSD.dateTime


class TestBuildGraphCommunitiesAndBridgeTags:
    def test_community_is_skos_collection_with_members(self, database_url: str) -> None:
        _seed_full_run(database_url, "run-0")
        graph = build_graph(database_url)

        collections = list(graph.subjects(RDF.type, SKOS.Collection))
        assert len(collections) == 1
        comm = collections[0]
        assert graph.value(comm, RDFS.label) == Literal("AI")
        assert graph.value(comm, STRATREP.summary) == Literal("A cluster about AI progress.")
        member_labels = {
            str(graph.value(m, SKOS.prefLabel)) for m in graph.objects(comm, SKOS.member)
        }
        assert member_labels == set(_TAGS)

    def test_bridge_tag_becomes_observation_node(self, database_url: str) -> None:
        _seed_full_run(database_url, "run-0")
        graph = build_graph(database_url)

        observations = list(graph.subjects(RDF.type, STRATREP.BridgeTagObservation))
        assert len(observations) == 1
        obs = observations[0]
        assert graph.value(obs, STRATREP["count"]) == Literal(4)
        assert graph.value(obs, STRATREP.rank) == Literal(1)
        tag = graph.value(obs, STRATREP.tag)
        assert str(graph.value(tag, SKOS.prefLabel)) == _TAGS[0]


class TestBuildGraphTopicRun:
    def test_urgency_score_and_strategic_bullets_share_topic_run_node(
        self, database_url: str
    ) -> None:
        _seed_full_run(database_url, "run-0")
        graph = build_graph(database_url)

        topic_runs = list(graph.subjects(RDF.type, STRATREP.TopicRun))
        assert len(topic_runs) == 1
        node = topic_runs[0]
        assert graph.value(node, STRATREP.hasUrgencyScore) == Literal(0.42)
        bullets = {str(b) for b in graph.objects(node, STRATREP.strategicBullet)}
        assert bullets == {
            "Invest in LLM tooling now.",
            "Target applied-AI roles.",
            "Build small deployed projects.",
        }


class TestBuildGraphOverview:
    def test_overview_bullet_included(self, database_url: str) -> None:
        _seed_full_run(database_url, "run-0")
        graph = build_graph(database_url)

        overviews = list(graph.subjects(RDF.type, STRATREP.CrossTopicOverview))
        assert len(overviews) == 1
        assert (
            overviews[0], STRATREP.overviewBullet, Literal("Overview bullet for run-0.")
        ) in graph


class TestSinceFilter:
    def test_full_rebuild_includes_both_runs(self, database_url: str) -> None:
        _seed_full_run(database_url, "run-0")
        _seed_full_run(database_url, "run-1")
        graph = build_graph(database_url)
        assert len(list(graph.subjects(RDF.type, PROV.Activity))) == 2

    def test_since_run_id_excludes_earlier_runs(self, database_url: str) -> None:
        _seed_full_run(database_url, "run-0")
        _seed_full_run(database_url, "run-1")
        graph = build_graph(database_url, since="run-1")
        runs = list(graph.subjects(RDF.type, PROV.Activity))
        assert runs == [BASE["run/run-1"]]

    def test_since_with_no_matching_runs_returns_empty_graph(self, database_url: str) -> None:
        _seed_full_run(database_url, "run-0")
        graph = build_graph(database_url, since="2099-01-01T00:00:00+00:00")
        assert len(graph) == 0


class TestExportRdf:
    def test_export_writes_parseable_turtle(self, database_url: str, tmp_path: Path) -> None:
        _seed_full_run(database_url, "run-0")
        output_path = tmp_path / "kg.ttl"

        triple_count = export_rdf(database_url, output_path)

        assert output_path.exists()
        assert triple_count > 0

        from rdflib import Graph
        reparsed = Graph()
        reparsed.parse(str(output_path), format="turtle")
        assert len(reparsed) == triple_count
