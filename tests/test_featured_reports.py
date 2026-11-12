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
        assert not unknown, f"{report.name}: unknown topic slugs {unknown}"


def test_each_report_has_featured_topics_and_no_duplicates() -> None:
    # report.topics is featured_topics + related_topics, so a slug listed in
    # both (or twice in one) shows up as a duplicate here.
    for report in featured_reports:
        assert report.featured_topics, report.name
        assert len(set(report.topics)) == len(report.topics), report.name


def test_report_names_unique_and_filename_safe() -> None:
    names = [r.name for r in featured_reports]
    assert len(set(names)) == len(names)
    for name in names:
        assert name and all(c.isalnum() or c in "-_" for c in name), name
