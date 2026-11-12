"""
Featured reports: alternate orderings of the main report that lead with one
topic.

Each entry renders `{name}-report.html` alongside index.html in --output-dir
(see renderer.render_report()). Topics listed in `topics` come first, in the
order given; every other topic from topic_order.py follows in its default
order; the cross-topic Strategic Overview comes last. Purely a re-ordering
of the same run's results -- no extra LLM calls, nothing persisted.

Every slug in `topics` must exist in topic_order.py (enforced by
tests/test_featured_reports.py).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class FeaturedReport:
    name: str                 # filename stem: "retail" -> retail-report.html
    title: str                # page heading
    topics: tuple[str, ...]   # topic slugs to lead with, in order; featured topic first


featured_reports: tuple[FeaturedReport, ...] = (
    FeaturedReport(
        name='retail',
        title='Retail Report',
        topics=(
            'feeds_retail',
            'feeds_business',
            'feeds_business_strategy',
            'feeds_economics',
            'feeds_forex',
            'feeds_leadership',
        ),
    ),
)
