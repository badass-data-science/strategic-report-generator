"""
Builds an RDF (Turtle) export of the tracking database's accumulated
knowledge — articles, tags, community summaries, bridge tags, per-topic
strategic bullets, urgency scores, and cross-topic overviews.

This complements tag_graph.py's per-run co-occurrence JSON/HTML output
rather than replacing it: tag_graph.json/tag_graph.html keep working
exactly as before, computed the same way, for the same audience (a single
run's D3 viewer). This module instead reads the durable, cross-run archive
in the tracking database and gives it a standard, portable shape for a
multi-source knowledge base.

Reuses standard vocabularies rather than inventing new ones where one
already fits:
  - SKOS — tags as skos:Concept, Louvain communities as skos:Collection.
    Tags are already normalized to a canonical form by tag_normalizer.py
    before they reach the database, so this is mostly "give the existing
    vocabulary a formal RDF shape," not new modeling work.
  - PROV-O — every derived fact traces back to the run (prov:Activity)
    that produced it via prov:wasGeneratedBy, mirroring the run_id foreign
    key already threaded through every table in db.py.
  - schema.org — article bibliographic fields (headline, url, datePublished).

Everything domain-specific with no clean standard equivalent (topics,
urgency scores, bridge-tag observations, the cross-topic overview) lives
under a small custom namespace, STRATREP.
"""

import re
import sqlite3
from collections.abc import Sequence
from pathlib import Path

from rdflib import RDF, RDFS, XSD, Graph, Literal, Namespace, URIRef
from rdflib.namespace import PROV, SDO, SKOS

from .db import connect

BASE = Namespace("https://strategic-reports.local/kg/")
STRATREP = Namespace("https://strategic-reports.local/ontology#")


def _slug(text: str) -> str:
    """Turn arbitrary text into a URI-safe path segment."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return slug or "unknown"


def _placeholders(items: Sequence[object]) -> str:
    return ",".join("?" for _ in items)


def _run_ids_since(conn: sqlite3.Connection, since: str | None) -> list[str] | None:
    """
    Resolve --since (a run_id or an ISO timestamp) to the list of run_ids
    at or after that point. None means no filter (full rebuild).
    """
    if since is None:
        return None
    row = conn.execute("SELECT created_at FROM runs WHERE run_id = ?", (since,)).fetchone()
    cutoff = row[0] if row is not None else since
    rows = conn.execute(
        "SELECT run_id FROM runs WHERE created_at >= ? ORDER BY created_at", (cutoff,)
    ).fetchall()
    return [r[0] for r in rows]


def build_graph(db_path: Path, since: str | None = None) -> Graph:
    """
    Build an in-memory RDF graph from the tracking database.

    since=None rebuilds from every run in the database. since=<run_id or
    ISO timestamp> includes only runs at or after that point.
    """
    graph = Graph()
    graph.bind("skos", SKOS)
    graph.bind("prov", PROV)
    graph.bind("schema", SDO)
    graph.bind("stratrep", STRATREP)

    conn = connect(db_path)
    try:
        run_ids = _run_ids_since(conn, since)
        if run_ids is not None and not run_ids:
            return graph

        run_filter = f"WHERE run_id IN ({_placeholders(run_ids)})" if run_ids is not None else ""
        run_params = tuple(run_ids) if run_ids is not None else ()

        # --- Runs -> prov:Activity ---
        runs = conn.execute(
            f"SELECT run_id, created_at, article_count FROM runs {run_filter}", run_params
        ).fetchall()
        for run_id, created_at, article_count in runs:
            run_uri = BASE[f"run/{run_id}"]
            graph.add((run_uri, RDF.type, PROV.Activity))
            graph.add((run_uri, PROV.startedAtTime, Literal(created_at, datatype=XSD.dateTime)))
            graph.add((run_uri, STRATREP.articleCount, Literal(article_count)))

        # --- Articles -> schema:Article, tagged via STRATREP.hasTag ---
        articles = conn.execute(
            f"SELECT id, run_id, topic, title, link, publish_date FROM articles {run_filter}",
            run_params,
        ).fetchall()
        topic_uris: dict[str, URIRef] = {}
        for article_id, run_id, topic, title, link, publish_date in articles:
            article_uri = BASE[f"article/{article_id}"]
            run_uri = BASE[f"run/{run_id}"]
            topic_uri = topic_uris.setdefault(topic, BASE[f"topic/{_slug(topic)}"])
            graph.add((topic_uri, RDF.type, STRATREP.Topic))
            graph.add((topic_uri, RDFS.label, Literal(topic)))

            graph.add((article_uri, RDF.type, SDO.Article))
            graph.add((article_uri, SDO.headline, Literal(title)))
            graph.add((article_uri, SDO.url, URIRef(link)))
            graph.add(
                (article_uri, SDO.datePublished, Literal(publish_date, datatype=XSD.dateTime))
            )
            graph.add((article_uri, PROV.wasGeneratedBy, run_uri))
            graph.add((article_uri, STRATREP.aboutTopic, topic_uri))

        if articles:
            article_ids = [row[0] for row in articles]
            placeholders = _placeholders(article_ids)

            tag_rows = conn.execute(
                f"SELECT article_id, tag FROM article_tags WHERE article_id IN ({placeholders})",
                article_ids,
            ).fetchall()
            for article_id, tag in tag_rows:
                tag_uri = BASE[f"tag/{_slug(tag)}"]
                graph.add((tag_uri, RDF.type, SKOS.Concept))
                graph.add((tag_uri, SKOS.prefLabel, Literal(tag)))
                graph.add((BASE[f"article/{article_id}"], STRATREP.hasTag, tag_uri))

            bullet_rows = conn.execute(
                f"SELECT article_id, bullet_text FROM article_summary_bullets "
                f"WHERE article_id IN ({placeholders}) ORDER BY article_id, bullet_index",
                article_ids,
            ).fetchall()
            for article_id, bullet_text in bullet_rows:
                graph.add(
                    (BASE[f"article/{article_id}"], STRATREP.summaryBullet, Literal(bullet_text))
                )

        # --- Per-topic strategic bullets + urgency scores -> a shared TopicRun node ---
        strategic_bullets = conn.execute(
            f"SELECT run_id, topic, bullet_text FROM bullets {run_filter} "
            f"ORDER BY run_id, topic, bullet_index",
            run_params,
        ).fetchall()
        for run_id, topic, bullet_text in strategic_bullets:
            topic_run_uri = BASE[f"topic-run/{run_id}/{_slug(topic)}"]
            graph.add((topic_run_uri, RDF.type, STRATREP.TopicRun))
            graph.add((topic_run_uri, PROV.wasGeneratedBy, BASE[f"run/{run_id}"]))
            graph.add((topic_run_uri, STRATREP.aboutTopic, BASE[f"topic/{_slug(topic)}"]))
            graph.add((topic_run_uri, STRATREP.strategicBullet, Literal(bullet_text)))

        urgency_scores = conn.execute(
            f"SELECT run_id, topic, score FROM urgency_scores {run_filter}", run_params
        ).fetchall()
        for run_id, topic, score in urgency_scores:
            topic_run_uri = BASE[f"topic-run/{run_id}/{_slug(topic)}"]
            graph.add((topic_run_uri, RDF.type, STRATREP.TopicRun))
            graph.add((topic_run_uri, PROV.wasGeneratedBy, BASE[f"run/{run_id}"]))
            graph.add((topic_run_uri, STRATREP.aboutTopic, BASE[f"topic/{_slug(topic)}"]))
            graph.add((topic_run_uri, STRATREP.hasUrgencyScore, Literal(score)))

        # --- Community summaries -> skos:Collection of member tags ---
        communities = conn.execute(
            f"SELECT run_id, community_id, label, summary, article_count "
            f"FROM community_summaries {run_filter}",
            run_params,
        ).fetchall()
        for run_id, community_id, label, summary, article_count in communities:
            comm_uri = BASE[f"community/{run_id}/{community_id}"]
            graph.add((comm_uri, RDF.type, SKOS.Collection))
            graph.add((comm_uri, RDFS.label, Literal(label)))
            graph.add((comm_uri, STRATREP.summary, Literal(summary)))
            graph.add((comm_uri, STRATREP.articleCount, Literal(article_count)))
            graph.add((comm_uri, PROV.wasGeneratedBy, BASE[f"run/{run_id}"]))

        community_tags = conn.execute(
            f"SELECT run_id, community_id, tag FROM community_summary_tags {run_filter}",
            run_params,
        ).fetchall()
        for run_id, community_id, tag in community_tags:
            tag_uri = BASE[f"tag/{_slug(tag)}"]
            graph.add((tag_uri, RDF.type, SKOS.Concept))
            graph.add((tag_uri, SKOS.prefLabel, Literal(tag)))
            graph.add((BASE[f"community/{run_id}/{community_id}"], SKOS.member, tag_uri))

        # --- Bridge tags -> observation nodes, mirroring the DB's own
        # row-per-observation shape (see db.py's bridge_tags table) ---
        bridge_tags = conn.execute(
            f"SELECT id, run_id, tag, count, rank FROM bridge_tags {run_filter}", run_params
        ).fetchall()
        for row_id, run_id, tag, count, rank in bridge_tags:
            tag_uri = BASE[f"tag/{_slug(tag)}"]
            graph.add((tag_uri, RDF.type, SKOS.Concept))
            graph.add((tag_uri, SKOS.prefLabel, Literal(tag)))
            obs_uri = BASE[f"bridge-tag-observation/{row_id}"]
            graph.add((obs_uri, RDF.type, STRATREP.BridgeTagObservation))
            graph.add((obs_uri, STRATREP.tag, tag_uri))
            graph.add((obs_uri, PROV.wasGeneratedBy, BASE[f"run/{run_id}"]))
            # STRATREP.count would resolve to str.count (Namespace subclasses
            # str) instead of building a URI — bracket access avoids that.
            graph.add((obs_uri, STRATREP["count"], Literal(count)))
            graph.add((obs_uri, STRATREP.rank, Literal(rank)))

        # --- Cross-topic overview bullets (overview_archive.py) ---
        overviews = conn.execute(
            f"SELECT run_id, bullet_text FROM cross_topic_overviews {run_filter} "
            f"ORDER BY run_id, bullet_index",
            run_params,
        ).fetchall()
        for run_id, bullet_text in overviews:
            overview_uri = BASE[f"overview/{run_id}"]
            graph.add((overview_uri, RDF.type, STRATREP.CrossTopicOverview))
            graph.add((overview_uri, PROV.wasGeneratedBy, BASE[f"run/{run_id}"]))
            graph.add((overview_uri, STRATREP.overviewBullet, Literal(bullet_text)))
    finally:
        conn.close()

    return graph


def export_rdf(db_path: Path, output_path: Path, since: str | None = None) -> int:
    """
    Build the RDF graph from the database and serialize it to output_path
    as Turtle. Returns the number of triples written.
    """
    graph = build_graph(db_path, since=since)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    graph.serialize(destination=str(output_path), format="turtle")
    return len(graph)
