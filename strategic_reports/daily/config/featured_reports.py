"""
Featured reports: focused views of the main report built around a few
featured topics, with related topics for context.

Each entry renders its `filename` alongside index.html in --output-dir
(see renderer.render_report()). The page runs: featured topics (in the order
given, each highlighted), then an inline tag network over featured + related
topics, then related topics (in the order given). The cross-topic Strategic
Overview is index.html-only. Topics in neither list are left out. Purely a selection
from the same run's results -- no extra LLM calls, nothing persisted.

Every slug must exist in topic_order.py, appear at most once across both
lists, and featured_topics must be non-empty (enforced by
tests/test_featured_reports.py).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class FeaturedReport:
    filename: str                      # output file, written next to index.html
    heading: str                       # text of the page's <h1> (the date is appended)
    page_title: str                    # text of the HTML <title> (the date is appended)
    featured_topics: tuple[str, ...]   # topic slugs to feature, in order
    related_topics: tuple[str, ...] = ()  # topic slugs shown after the tag network, in order
    # Tag-network thresholds for this report's inline graph. They're applied
    # when the graph data is built (so they're also the lowest the page's
    # sliders can go) and set the sliders' starting positions. Defaults match
    # the standalone tag_graph.html's, which these never affect. Lower values
    # show more of the graph but grow it fast: 1/1 embeds every tag and every
    # co-occurring pair.
    min_co_occurrences: int = 2   # min articles a tag pair must share to draw an edge
    min_article_count: int = 3    # min articles a tag must appear in to be drawn

    @property
    def topics(self) -> tuple[str, ...]:
        """Every topic on the page: featured first, then related."""
        return self.featured_topics + self.related_topics


featured_reports: tuple[FeaturedReport, ...] = (
    FeaturedReport(
        filename='retail-strategy-report.html',
        heading='Retail & Fashion - Daily Strategic Report',
        page_title='Retail & Fashion - Strategic Report',
        featured_topics=(
            'feeds_retail',
            'feeds_fashion',
        ),
        related_topics=(
            'feeds_leadership',
            'feeds_business',
            'feeds_business_strategy',
            'feeds_economics',
        ),
        min_co_occurrences = 2,
        min_article_count = 2,
    ),
)
