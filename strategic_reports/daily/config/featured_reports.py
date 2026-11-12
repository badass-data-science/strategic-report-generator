"""
Featured reports: focused views of the main report built around a few
featured topics, with related topics for context.

Each entry renders `{name}-report.html` alongside index.html in --output-dir
(see renderer.render_report()). The page runs: featured topics (in the order
given, each highlighted), then an inline tag network over featured + related
topics, then related topics (in the order given), then the cross-topic
Strategic Overview. Topics in neither list are left out. Purely a selection
from the same run's results -- no extra LLM calls, nothing persisted.

Every slug must exist in topic_order.py, appear at most once across both
lists, and featured_topics must be non-empty (enforced by
tests/test_featured_reports.py).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class FeaturedReport:
    name: str                          # filename stem: "retail" -> retail-report.html
    title: str                         # page heading
    featured_topics: tuple[str, ...]   # topic slugs to feature, in order
    related_topics: tuple[str, ...] = ()  # topic slugs shown after the tag network, in order

    @property
    def topics(self) -> tuple[str, ...]:
        """Every topic on the page: featured first, then related."""
        return self.featured_topics + self.related_topics


featured_reports: tuple[FeaturedReport, ...] = (
    FeaturedReport(
        name='retail',
        title='Retail & Fashion Industry - Daily Strategic Report',
        featured_topics=(
            'feeds_retail',
            'feeds_fashion',
        ),
        related_topics=(
            'feeds_business',
            'feeds_business_strategy',
            'feeds_leadership',
            'feeds_economics',
            'feeds_ai',
            'feeds_data_science',
        ),
    ),
)
