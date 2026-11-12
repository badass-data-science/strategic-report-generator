"""
Config sanity checks for config/featured_reports.py -- a typo'd slug would
otherwise be silently skipped by renderer._select_for_featured().
"""

from strategic_reports.daily.config.featured_reports import featured_reports
from strategic_reports.daily.config.topic_order import list_directories_and_titles

_KNOWN_SLUGS = {item["slug"] for item in list_directories_and_titles}


def test_every_featured_slug_is_an_active_topic() -> None:
    for report in featured_reports:
        unknown = [s for s in report.topics if s not in _KNOWN_SLUGS]
        assert not unknown, f"{report.filename}: unknown topic slugs {unknown}"


def test_each_report_has_featured_topics_and_no_duplicates() -> None:
    # report.topics is featured_topics + related_topics, so a slug listed in
    # both (or twice in one) shows up as a duplicate here.
    for report in featured_reports:
        assert report.featured_topics, report.filename
        assert len(set(report.topics)) == len(report.topics), report.filename


# Files render_report() / write_tag_graph() already write into --output-dir;
# a featured report must never overwrite one of them.
_RESERVED_FILENAMES = {
    "index.html", "tag_graph.html", "tag_graph.json", "tag_graph_display.json",
    "systems_signals.json", "knowledge_graph.ttl", "db_status.html",
}


def test_filenames_unique_safe_and_not_reserved() -> None:
    names = [r.filename for r in featured_reports]
    assert len(set(names)) == len(names), names
    for name in names:
        assert name.endswith(".html") and len(name) > len(".html"), name
        assert all(c.isalnum() or c in "-_." for c in name), name  # no paths/spaces
        assert name not in _RESERVED_FILENAMES, name
        assert not name.endswith("_summaries.html"), name  # per-topic pages


def test_heading_and_page_title_set() -> None:
    for report in featured_reports:
        assert report.heading.strip(), report.filename
        assert report.page_title.strip(), report.filename


def test_tag_graph_thresholds_are_positive_ints() -> None:
    # Both feed build_display_graph() and the viewer's sliders (min="1").
    for report in featured_reports:
        for field in ("min_co_occurrences", "min_article_count"):
            value = getattr(report, field)
            assert isinstance(value, int) and value >= 1, (report.filename, field, value)
