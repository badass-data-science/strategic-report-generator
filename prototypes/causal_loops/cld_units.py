"""
Prototype (not wired into the pipeline): hypothesis-first causal loop diagram at the
unit level, where a "unit" is either a user-defined topic or a community
from a frozen Louvain partition of the cumulative tag graph.

  1. Build units + per-run series (share of articles, CLR-transformed so the
     compositional "shares sum to 1" effect doesn't manufacture negative
     correlations; for topics also urgency score, partial-correlated against
     the leave-one-out mean like systems_signals does).
  2. LLM proposes a CLD among the units BLIND -- it never sees the
     correlations, so the check below is independent. k samples, keep links
     >= min_agree samples agree on.
  3. Each consensus link is checked against the data: supported /
     contradicted / weak / not detected, per measure.
  4. Render with causal-loop-diagram-inator, edges colored by evidence.

Run from the strategic-report-generator repo root:
    source ~/environment_variables/strategic_daily_report.sh
    .venv/bin/python prototypes/causal_loops/cld_units.py --out-dir /tmp/cld_out

Rendering needs causal-loop-diagram-inator's src/ (see --cld-src) plus the d2
and inkscape CLIs. See README.md alongside this file for results and caveats.
"""

import argparse
import asyncio
import json
import math
import os
import re
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import permutations
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import instructor
import networkx as nx
from pydantic import BaseModel, Field, model_validator
from strategic_reports.daily.core.db import get_connection
from strategic_reports.daily.core.llm_client import LLMClient
from strategic_reports.daily.core.systems_signals import (
    MIN_RUNS_FOR_LAG,
    benjamini_hochberg_qvalues,
    lagged_partial_pearson,
    lagged_pearson,
    pearson_p_value,
)

DEFAULT_CLD_SRC = Path.home() / "Desktop/projects/causal-loop-diagram-inator/src"
MAX_LAG = 3
FDR_Q = 0.05
MIN_R_SUPPORT = 0.4
MIN_R_WEAK = 0.3
MIN_COMMUNITY_TAGS = 5
# Defaults for the --target-communities / --max-links / --max-loops /
# --max-loop-length flags. MAX_LINKS is read by SystemHypothesis's validator
# (so instructor retries an over-long answer) and overridden by main() from
# --max-links: LLMClient doesn't forward instructor's validation context, so a
# module-level setting is the simplest way to parameterise it.
TARGET_COMMUNITIES = (15, 20)
MAX_LINKS = 22
MAX_LOOPS = 10
MAX_LOOP_LENGTH = 6
# Unit pairs at or above either threshold are "near-duplicates": largely the
# same coverage, so same-run co-movement between them is expected rather than
# evidence. Article overlap is the Jaccard of the two units' article sets
# (always 0 for topics, where each article has one topic). Source overlap is
# the histogram intersection of their source-domain mixes; it's applied to
# topics only, where it surfaces shared feeds -- across communities a few big
# publishers feed most of them (median 0.52 on 2026-10-13) and it didn't track
# co-movement at all.
OVERLAP_ARTICLES = 0.4
OVERLAP_SOURCES = 0.05
STATUS_COLORS = {
    "supported": "#2e7d32",
    "weak": "#f9a825",
    "mixed": "#6a1b9a",
    "expected": "#64b5f6",  # only lag-0 evidence, between near-duplicate units
    "contradicted": "#c62828",
    "not_detected": "#9e9e9e",
}


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------


@dataclass
class Data:
    runs: list[str]
    as_of: str | None  # human-readable cutoff, None = full history
    article_topic: dict[int, str]  # article_id -> topic
    article_run: dict[int, str]
    article_title: dict[int, str]
    article_domain: dict[int, str]
    article_tags: dict[int, set[str]]
    tag_count: dict[str, int]
    edge_weight: Counter[tuple[str, str]]
    urgency: dict[tuple[str, str], float]  # (run, topic) -> score
    bad_runs: set[str]
    latest_bullets: dict[str, list[str]]  # topic -> latest run's strategic bullets
    tag_topic_counts: dict[str, Counter[str]]  # topic -> tag counts
    overview: list[str]
    bridge: list[tuple[str, list[str]]]
    recent_communities: list[tuple[set[str], str, str]]  # (tags, label, summary)


def _parse_ts(value: str) -> datetime:
    ts = datetime.fromisoformat(value)
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def resolve_as_of(
    runs_rows: list[tuple[str, str, int]], as_of: str | None
) -> tuple[list[tuple[str, str, int]], str | None]:
    """
    Keep only runs at or before --as-of: a run_id (that run and everything
    before it), an ISO timestamp (naive means UTC), or a bare YYYY-MM-DD date
    (every run through the end of that UTC day). Compared as parsed datetimes,
    not strings, since created_at is stored as ISO text.
    """
    if as_of is None:
        return runs_rows, None
    by_id = {r: created for r, created, _ in runs_rows}
    if as_of in by_id:
        cutoff = _parse_ts(by_id[as_of])
        label = f"run {as_of} ({by_id[as_of]})"
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of):
        cutoff = _parse_ts(as_of) + timedelta(days=1) - timedelta(microseconds=1)
        label = f"end of {as_of} UTC"
    else:
        try:
            cutoff = _parse_ts(as_of)
        except ValueError:
            sys.exit(f"--as-of {as_of!r} is neither a known run_id nor an ISO timestamp/date")
        label = cutoff.isoformat()
    kept = [row for row in runs_rows if _parse_ts(row[1]) <= cutoff]
    if not kept:
        sys.exit(f"--as-of {as_of!r}: no runs at or before {label}")
    return kept, label


def load(database_url: str, as_of: str | None = None) -> Data:
    with get_connection(database_url) as c:
        all_runs = c.execute(
            "SELECT run_id, created_at, article_count FROM runs ORDER BY created_at"
        ).fetchall()
        kept, as_of_label = resolve_as_of(all_runs, as_of)
        runs_rows = [(r, n) for r, _, n in kept]
        runs = [r for r, _ in runs_rows]
        # Every query below is restricted to `runs`, so an --as-of draft sees
        # exactly the archive as it stood then (history, partition and context).
        arts = c.execute(
            "SELECT id, run_id, topic, title FROM articles WHERE run_id = ANY(%s)", (runs,)
        ).fetchall()
        links = c.execute(
            "SELECT id, link FROM articles WHERE run_id = ANY(%s)", (runs,)
        ).fetchall()
        at = c.execute(
            "SELECT article_id, tag FROM article_tags WHERE run_id = ANY(%s)", (runs,)
        ).fetchall()
        tag_count = dict(
            c.execute(
                "SELECT tag, SUM(count) FROM tag_counts WHERE run_id = ANY(%s) GROUP BY tag",
                (runs,),
            ).fetchall()
        )
        edges = c.execute(
            "SELECT tag_a, tag_b, SUM(weight) FROM tag_edges WHERE run_id = ANY(%s) "
            "GROUP BY tag_a, tag_b",
            (runs,),
        ).fetchall()
        urg = c.execute(
            "SELECT run_id, topic, score FROM urgency_scores WHERE run_id = ANY(%s)", (runs,)
        ).fetchall()
        bullets = c.execute(
            "SELECT DISTINCT ON (topic, bullet_index) topic, bullet_index, bullet_text "
            "FROM bullets WHERE run_id = ANY(%s) ORDER BY topic, bullet_index, created_at DESC",
            (runs,),
        ).fetchall()
        tt = c.execute(
            "SELECT topic, tag, COUNT(*) FROM tag_topics WHERE run_id = ANY(%s) "
            "GROUP BY topic, tag",
            (runs,),
        ).fetchall()
        last = runs[-1]
        overview = [
            r[0]
            for r in c.execute(
                "SELECT bullet_text FROM cross_topic_overviews WHERE run_id = %s "
                "ORDER BY bullet_index",
                (last,),
            ).fetchall()
        ]
        bridge_rows = c.execute(
            "SELECT b.tag, array_agg(DISTINCT t.topic) FROM bridge_tags b "
            "JOIN bridge_tag_topics t ON t.run_id = b.run_id AND t.tag = b.tag "
            "WHERE b.run_id = %s GROUP BY b.tag, b.rank ORDER BY b.rank",
            (last,),
        ).fetchall()
        recent = runs[-5:]
        cst = c.execute(
            "SELECT run_id, community_id, tag FROM community_summary_tags WHERE run_id = ANY(%s)",
            (recent,),
        ).fetchall()
        cs = c.execute(
            "SELECT run_id, community_id, label, summary FROM community_summaries "
            "WHERE run_id = ANY(%s)",
            (recent,),
        ).fetchall()

    present = {r for _, r, _, _ in arts}
    bad = {r for r, n in runs_rows if n > 0 and r not in present}
    tags_by_article: dict[int, set[str]] = defaultdict(set)
    for aid, tag in at:
        tags_by_article[aid].add(tag)
    latest_bullets: dict[str, list[str]] = defaultdict(list)
    for topic, _, text in sorted(bullets, key=lambda b: (b[0], b[1])):
        latest_bullets[topic].append(text)
    ttc: dict[str, Counter[str]] = defaultdict(Counter)
    for topic, tag, n in tt:
        ttc[topic][tag] = n
    comm_tags: dict[tuple[str, int], set[str]] = defaultdict(set)
    for r, cid, tag in cst:
        comm_tags[(r, cid)].add(tag)

    return Data(
        runs=runs,
        as_of=as_of_label,
        article_topic={a: t for a, _, t, _ in arts},
        article_run={a: r for a, r, _, _ in arts},
        article_title={a: ti for a, _, _, ti in arts},
        article_domain={a: urlparse(lnk).netloc.lower().removeprefix("www.") for a, lnk in links},
        article_tags=tags_by_article,
        tag_count=tag_count,
        edge_weight=Counter({(a, b): w for a, b, w in edges}),
        urgency={(r, t): s for r, t, s in urg},
        bad_runs=bad,
        latest_bullets=latest_bullets,
        tag_topic_counts=ttc,
        overview=overview,
        bridge=[(t, list(ts)) for t, ts in bridge_rows],
        recent_communities=[(comm_tags[(r, cid)], lab, summ) for r, cid, lab, summ in cs],
    )


# --------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------


@dataclass
class Unit:
    uid: str
    title: str
    top_tags: list[str]
    description: list[str]
    articles: set[int]  # every archived article belonging to this unit
    is_topic: bool = False
    topic: str | None = None


def topic_units(d: Data) -> list[Unit]:
    topics = sorted(set(d.article_topic.values()))
    units = []
    for i, t in enumerate(topics, 1):
        arts = {a for a, at in d.article_topic.items() if at == t}
        recent = sorted(a for a in arts if d.article_run[a] == d.runs[-1])[:4]
        units.append(
            Unit(
                uid=f"T{i}",
                title=t,
                is_topic=True,
                topic=t,
                top_tags=[x for x, _ in d.tag_topic_counts[t].most_common(10)],
                description=[
                    f"Latest strategic bullet: {b}" for b in d.latest_bullets.get(t, [])[:4]
                ]
                + [f"Recent headline: {d.article_title[a]}" for a in recent],
                articles=arts,
            )
        )
    return units


def louvain_partition(d: Data, resolution: float, seed: int = 42) -> list[set[str]]:
    g = nx.Graph()
    for (a, b), w in d.edge_weight.items():
        if w >= 5 and d.tag_count.get(a, 0) >= 10 and d.tag_count.get(b, 0) >= 10:
            g.add_edge(a, b, weight=w)
    comms = nx.community.louvain_communities(g, weight="weight", resolution=resolution, seed=seed)
    return sorted((c for c in comms if len(c) >= MIN_COMMUNITY_TAGS), key=len, reverse=True)


def _best_match_jaccard(a: list[set[str]], b: list[set[str]]) -> float:
    return statistics.median(max(len(x & y) / len(x | y) for y in b) for x in a)


def community_units(
    d: Data, log: list[str], target: tuple[int, int] = TARGET_COMMUNITIES
) -> list[Unit]:
    lo, hi = target
    chosen: tuple[float, list[set[str]]] | None = None
    sweep = []
    # Closest count wins if no resolution lands in [lo, hi]. Higher resolutions
    # also split off more communities below MIN_COMMUNITY_TAGS, which are dropped.
    for res in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 16.0):
        p = louvain_partition(d, res)
        sweep.append((res, len(p)))
        if chosen is None or abs(len(p) - (lo + hi) / 2) < abs(len(chosen[1]) - (lo + hi) / 2):
            chosen = (res, p)
        if lo <= len(p) <= hi:
            chosen = (res, p)
            break
    assert chosen is not None
    res, partition = chosen
    stability = _best_match_jaccard(partition, louvain_partition(d, res, seed=1))
    covered = sum(len(c) for c in partition)
    log.append(f"resolution sweep (resolution, communities>={MIN_COMMUNITY_TAGS} tags): {sweep}")
    log.append(
        f"chose resolution={res}: {len(partition)} communities, {covered} tags, "
        f"seed-stability (median best-match Jaccard vs seed=1) {stability:.2f}"
    )

    tag_to_k = {t: i for i, c in enumerate(partition) for t in c}
    arts_by_k: dict[int, set[int]] = defaultdict(set)
    for aid, tags in d.article_tags.items():
        for k in {tag_to_k[t] for t in tags if t in tag_to_k}:
            arts_by_k[k].add(aid)

    units = []
    for k, comm in enumerate(partition):
        top = sorted(comm, key=lambda t: -d.tag_count.get(t, 0))[:10]
        topic_mix = Counter(d.article_topic[a] for a in arts_by_k[k])
        total = sum(topic_mix.values()) or 1
        mix = ", ".join(f"{t} {n / total:.0%}" for t, n in topic_mix.most_common(4))
        overlaps = sorted(
            d.recent_communities, key=lambda rc: -len(rc[0] & comm) / (len(rc[0] | comm) or 1)
        )[:2]
        recent = sorted(
            (a for a in arts_by_k[k] if d.article_run[a] == d.runs[-1]),
            key=lambda a: -len(d.article_tags[a] & comm),
        )[:4]
        units.append(
            Unit(
                uid=f"K{k + 1}",
                title=" / ".join(top[:3]),
                top_tags=top,
                description=[f"Defined-topic mix of its articles: {mix}"]
                + [f"Closest recent daily community '{lab}': {summ}" for _, lab, summ in overlaps]
                + [f"Recent headline: {d.article_title[a]}" for a in recent],
                articles=arts_by_k[k],
            )
        )
    return units


# --------------------------------------------------------------------------
# Series + evidence
# --------------------------------------------------------------------------

Series = dict[str, list[float | None]]


def share_clr_series(d: Data, units: list[Unit]) -> Series:
    """Centered log-ratio of each unit's article share per run (pseudo-count 0.5)."""
    per_run_total = Counter(d.article_run.values())
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for u in units:
        for a in u.articles:
            counts[d.article_run[a]][u.uid] += 1
    out: Series = {u.uid: [None] * len(d.runs) for u in units}
    for i, r in enumerate(d.runs):
        n = per_run_total[r]
        if r in d.bad_runs or n == 0:
            continue
        logs = {u.uid: math.log((counts[r][u.uid] + 0.5) / n) for u in units}
        mean = sum(logs.values()) / len(logs)
        for uid, v in logs.items():
            out[uid][i] = v - mean
    return out


def urgency_series(d: Data, units: list[Unit]) -> Series:
    out: Series = {}
    for u in units:
        out[u.uid] = [
            None if r in d.bad_runs else d.urgency.get((r, u.topic or "")) for r in d.runs
        ]
    return out


def _loo_control(series: Series, uid: str, n_runs: int) -> list[float | None]:
    ctrl: list[float | None] = []
    for i in range(n_runs):
        vals = [s[i] for k, s in series.items() if k != uid and s[i] is not None]
        ctrl.append(sum(vals) / len(vals) if len(vals) >= 2 else None)  # type: ignore[arg-type]
    return ctrl


@dataclass
class Test:
    a: str
    b: str
    lag: int
    r: float
    n: int
    p: float
    q: float = 1.0


def scan(series: Series, partial: bool, n_runs: int) -> dict[tuple[str, str, int], Test]:
    ctrl = {u: _loo_control(series, u, n_runs) for u in series} if partial else {}
    tests: list[Test] = []
    for a, b in permutations(series, 2):
        for lag in range(MAX_LAG + 1):
            out = (
                lagged_partial_pearson(series[a], series[b], ctrl[a], ctrl[b], lag)
                if partial
                else lagged_pearson(series[a], series[b], lag)
            )
            if out is None:
                continue
            r, n = out
            tests.append(Test(a, b, lag, r, n, pearson_p_value(r, n, 2 if partial else 0)))
    for t, q in zip(tests, benjamini_hochberg_qvalues([t.p for t in tests]), strict=True):
        t.q = q
    return {(t.a, t.b, t.lag): t for t in tests}


def classify(
    tests: dict[tuple[str, str, int], Test], a: str, b: str, sign: int
) -> tuple[str, Test | None]:
    cands = [tests[(a, b, lag)] for lag in range(MAX_LAG + 1) if (a, b, lag) in tests]
    if not cands:
        return "not_detected", None
    sig = [t for t in cands if t.q <= FDR_Q and abs(t.r) >= MIN_R_SUPPORT]
    agree = [t for t in sig if t.r * sign > 0]
    oppose = [t for t in sig if t.r * sign < 0]
    best = lambda ts: max(ts, key=lambda t: abs(t.r))  # noqa: E731
    if agree and oppose:
        return "mixed", best(agree)
    if agree:
        return "supported", best(agree)
    if oppose:
        return "contradicted", best(oppose)
    weak = [t for t in cands if t.r * sign > 0 and abs(t.r) >= MIN_R_WEAK and t.p <= 0.05]
    if weak:
        return "weak", best(weak)
    return "not_detected", best(cands)


_STRONG = {"supported", "contradicted", "mixed"}


def classify_pair(
    tests: dict[tuple[str, str, int], Test], a: str, b: str, sign: int, near_duplicate: bool
) -> tuple[str, Test | None]:
    """
    classify(), except that between near-duplicate units lag-0 evidence alone
    can't make a verdict: shared coverage makes same-run co-movement expected.
    Lagged evidence still counts; failing that, a strong lag-0-only verdict
    becomes "expected".
    """
    status, best = classify(tests, a, b, sign)
    if not near_duplicate or status not in _STRONG:
        return status, best
    lagged = {k: t for k, t in tests.items() if k[2] > 0}
    lag_status, lag_best = classify(lagged, a, b, sign)
    if lag_status in _STRONG:
        return lag_status, lag_best
    return "expected", best


def combine(statuses: list[str]) -> str:
    s = set(statuses)
    if "supported" in s and "contradicted" in s or "mixed" in s:
        return "mixed"
    for status in ("supported", "contradicted", "expected", "weak"):
        if status in s:
            return status
    return "not_detected"


# --------------------------------------------------------------------------
# LLM proposal (blind to the data)
# --------------------------------------------------------------------------


class UnitVariable(BaseModel):
    unit_id: str
    name: str = Field(
        description="2-5 word Title Case quantity that can rise or fall, e.g. "
        "'Defense Procurement Spending'. No punctuation other than hyphens."
    )
    direction: Literal["+", "-"] = Field(
        description="'+' if this variable rises when the unit gets MORE news coverage/urgency, "
        "'-' if it falls (e.g. 'Market Stability' for a unit about market turmoil)."
    )


class ProposedLink(BaseModel):
    source_unit: str
    target_unit: str
    polarity: Literal["+", "-"] = Field(description="Between the two VARIABLES as you named them.")
    delayed: bool
    mechanism: str = Field(description="One sentence: the real-world mechanism.")
    confidence: Literal["low", "medium", "high"]


class SystemHypothesis(BaseModel):
    variables: list[UnitVariable]
    links: list[ProposedLink]

    @model_validator(mode="after")
    def _consistent(self) -> "SystemHypothesis":
        ids = [v.unit_id for v in self.variables]
        if len(ids) != len(set(ids)):
            raise ValueError("each unit_id may appear at most once in variables")
        declared = set(ids)
        bad = {
            u for lk in self.links for u in (lk.source_unit, lk.target_unit) if u not in declared
        }
        if bad:
            raise ValueError(f"links reference units with no declared variable: {sorted(bad)}")
        if any(lk.source_unit == lk.target_unit for lk in self.links):
            raise ValueError("self-links are not allowed")
        if len(self.links) > MAX_LINKS:
            raise ValueError(f"{len(self.links)} links; propose at most {MAX_LINKS}")
        return self


def system_prompt(max_links: int) -> str:
    count = f"{min(8, max_links)}-{max_links}" if max_links > 8 else str(max_links)
    return f"""\
You are a systems-thinking analyst. Draft a causal loop diagram (CLD) of how \
the real-world dynamics behind the following news units drive one another.

Rules:
- Each unit gets at most one variable: a measurable quantity that can rise or \
fall, tied to that unit. Omit units that don't participate in any link.
- Propose {count} directed causal links, each with a \
concrete mechanism. Prefer links that form genuine feedback loops. Include \
balancing (negative) feedback where real counter-pressures exist -- real \
systems have both.
- Mark a link `delayed` if its effect takes weeks or longer.
- You are NOT shown any statistics. Your hypothesis will be tested afterwards \
against the archive's day-to-day time series, so propose what you actually \
believe, not what you expect to be easy to confirm.
"""


@dataclass
class Overlap:
    a: str
    b: str
    articles: float  # Jaccard of the two units' article sets
    sources: float  # histogram intersection of their source-domain mixes
    reason: str | None  # why it's a near-duplicate, None if it isn't

    @property
    def flagged(self) -> bool:
        return self.reason is not None


def unit_overlaps(
    d: Data, units: list[Unit], use_sources: bool, art_threshold: float, src_threshold: float
) -> list[Overlap]:
    mixes = {u.uid: Counter(d.article_domain[a] for a in u.articles) for u in units}
    totals = {uid: sum(m.values()) or 1 for uid, m in mixes.items()}
    out = []
    for i, ua in enumerate(units):
        for ub in units[i + 1 :]:
            union = len(ua.articles | ub.articles)
            art = len(ua.articles & ub.articles) / union if union else 0.0
            ma, mb = mixes[ua.uid], mixes[ub.uid]
            src = sum(
                min(ma[k] / totals[ua.uid], mb[k] / totals[ub.uid]) for k in ma.keys() & mb.keys()
            )
            reason = None
            if art >= art_threshold:
                reason = f"share {art:.0%} of their articles"
            elif use_sources and src >= src_threshold:
                reason = f"partly draw on the same sources ({src:.0%} source overlap)"
            out.append(Overlap(ua.uid, ub.uid, art, src, reason))
    return out


def build_prompt(units: list[Unit], d: Data, kind: str, near_dupes: list[Overlap]) -> str:
    lines = [f"## Units ({kind})"]
    for u in units:
        lines.append(f"\n### {u.uid}: {u.title}")
        lines.append("Top tags: " + ", ".join(u.top_tags))
        lines += [f"- {x}" for x in u.description]
    lines.append("\n## Latest cross-topic strategic overview")
    lines += [f"- {b}" for b in d.overview]
    if d.bridge:
        lines.append("\n## Latest bridge tags (tags spanning 3+ defined topics)")
        lines += [f"- {t}: {', '.join(ts)}" for t, ts in d.bridge[:10]]
    if near_dupes:
        lines.append(
            "\n## Near-duplicate unit pairs\n"
            "These pairs largely cover the same news, so they will rise and fall together for "
            "that reason alone. Don't build a loop solely between the two units of a pair. If "
            "you link them at all, the mechanism must not depend on their being the same "
            "coverage; using only one unit of a pair is fine."
        )
        lines += [f"- {o.a} and {o.b} {o.reason}" for o in near_dupes]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Consensus
# --------------------------------------------------------------------------


@dataclass
class CLink:
    src: str
    tgt: str
    coverage_sign: int  # expected sign of correlation between the units' series
    agreement: int
    delayed: bool
    mechanism: str
    confidence: str
    polarity: str = "+"  # between displayed variables
    status: str = "not_detected"
    evidence: dict[str, tuple[str, Test | None]] = field(default_factory=dict)


def consensus(
    samples: list[SystemHypothesis], valid: set[str], min_agree: int, warn: list[str]
) -> tuple[list[CLink], dict[str, tuple[str, str]]]:
    votes: dict[tuple[str, str, int], list[tuple[int, ProposedLink]]] = defaultdict(list)
    names: dict[str, Counter[tuple[str, str]]] = defaultdict(Counter)
    for i, h in enumerate(samples):
        dirs = {v.unit_id: (1 if v.direction == "+" else -1) for v in h.variables}
        for v in h.variables:
            if v.unit_id in valid:
                names[v.unit_id][(v.name, v.direction)] += 1
        for lk in h.links:
            if lk.source_unit not in valid or lk.target_unit not in valid:
                warn.append(
                    f"sample {i + 1}: dropped link with unknown unit "
                    f"{lk.source_unit}->{lk.target_unit}"
                )
                continue
            sign = (1 if lk.polarity == "+" else -1) * dirs[lk.source_unit] * dirs[lk.target_unit]
            votes[(lk.source_unit, lk.target_unit, sign)].append((i, lk))

    display = {u: c.most_common(1)[0][0] for u, c in names.items()}
    out = []
    for (src, tgt, sign), vs in votes.items():
        n = len({i for i, _ in vs})
        if n < min_agree:
            continue
        ls = [lk for _, lk in vs]
        d_src = 1 if display[src][1] == "+" else -1
        d_tgt = 1 if display[tgt][1] == "+" else -1
        out.append(
            CLink(
                src,
                tgt,
                sign,
                n,
                delayed=Counter(lk.delayed for lk in ls).most_common(1)[0][0],
                mechanism=ls[0].mechanism,
                confidence=Counter(lk.confidence for lk in ls).most_common(1)[0][0],
                polarity="+" if sign * d_src * d_tgt > 0 else "-",
            )
        )
    return out, display


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


def cld_name(name: str) -> str:
    return re.sub(r"[:,;#>]|->", " ", name).strip()


def node_names(links: list[CLink], display: dict[str, tuple[str, str]]) -> dict[str, str]:
    used = {u for lk in links for u in (lk.src, lk.tgt)}
    raw = {u: cld_name(display[u][0]) for u in used}
    dup = {n for n, c in Counter(raw.values()).items() if c > 1}
    return {u: f"{n} ({u})" if n in dup else n for u, n in raw.items()}


def loops_of(
    links: list[CLink],
    nn: dict[str, str],
    max_loops: int = MAX_LOOPS,
    max_length: int = MAX_LOOP_LENGTH,
) -> list[list[str]]:
    g = nx.DiGraph((nn[lk.src], nn[lk.tgt]) for lk in links)
    cycles = sorted(nx.simple_cycles(g, length_bound=max_length), key=len)
    return cycles[:max_loops]


def to_cld(links: list[CLink], nn: dict[str, str], title: str) -> str:
    # No `loop:` lines on purpose: cld draws an R/B icon (and a legend entry)
    # for each one, and the diagrams are cleaner without them. Loops are still
    # listed in the markdown report via loop_lines().
    out = [f"title: {title}", ""]
    for lk in links:
        delay = " delay" if lk.delayed else ""
        out.append(f"{nn[lk.src]} -> {nn[lk.tgt]}: {lk.polarity}{delay} {STATUS_COLORS[lk.status]}")
    return "\n".join(out) + "\n"


def loop_lines(
    loops: list[list[str]],
    links: list[CLink],
    nn: dict[str, str],
    near_dupes: frozenset[frozenset[str]] = frozenset(),
) -> list[str]:
    """Markdown bullets: each loop's R/B type (from edge polarities) and edge evidence."""
    by_pair = {(nn[lk.src], nn[lk.tgt]): lk for lk in links}
    lines = []
    for c in loops:
        edges = [by_pair[(c[i], c[(i + 1) % len(c)])] for i in range(len(c))]
        kind = "B" if sum(e.polarity == "-" for e in edges) % 2 else "R"
        sts = Counter(e.status for e in edges)
        dupes = sorted(
            "/".join(sorted((e.src, e.tgt)))
            for e in edges
            if frozenset((e.src, e.tgt)) in near_dupes
        )
        lines.append(
            f"- **{kind}** {' → '.join(c + [c[0]])} — edge evidence: "
            + ", ".join(f"{s} {n}" for s, n in sts.items())
            + (
                f" — **runs through near-duplicate units {', '.join(sorted(set(dupes)))}**"
                if dupes
                else ""
            )
        )
    return lines or ["- none"]


def fmt_ev(status: str, t: Test | None) -> str:
    if t is None:
        return status
    return f"{status} (r={t.r:+.2f}, lag {t.lag}, q={t.q:.3f})"


def to_markdown(
    kind: str,
    units: list[Unit],
    links: list[CLink],
    nn: dict[str, str],
    loops: list[list[str]],
    measures: list[str],
    unproposed: list[Test],
    log: list[str],
    k: int,
    min_agree: int,
    model: str,
    overlaps: list[Overlap],
) -> str:
    md = [
        f"# Causal loop hypothesis: {kind} (prototype)",
        "",
        f"Model `{model}`, {k} blind samples, links kept at >= {min_agree}/{k} agreement, "
        f"then checked against: {', '.join(measures)}. Edge colors: "
        + ", ".join(f"{s} {c}" for s, c in STATUS_COLORS.items())
        + ".",
        "",
    ]
    md += [f"- {x}" for x in log] + [
        "",
        "## Units",
        "",
        "| Id | Unit | Top tags |",
        "|---|---|---|",
    ]
    md += [f"| {u.uid} | {u.title} | {', '.join(u.top_tags[:8])} |" for u in units]
    status_counts = Counter(lk.status for lk in links)
    md += [
        "",
        f"## Links ({len(links)}; "
        + ", ".join(f"{s} {n}" for s, n in status_counts.most_common())
        + ")",
        "",
        "| Link | Pol | Agree | Status | " + " | ".join(measures) + " | Mechanism |",
        "|---|---|---|---|" + "---|" * len(measures) + "---|",
    ]
    order = list(STATUS_COLORS)
    for lk in sorted(links, key=lambda lk: (order.index(lk.status), -lk.agreement)):
        ev = " | ".join(fmt_ev(*lk.evidence[m]) for m in measures)
        md.append(
            f"| {nn[lk.src]} ({lk.src}) → {nn[lk.tgt]} ({lk.tgt}) | {lk.polarity} | "
            f"{lk.agreement}/{k} | **{lk.status}** | {ev} | {lk.mechanism} |"
        )
    flagged = [o for o in overlaps if o.flagged]
    near = frozenset(frozenset((o.a, o.b)) for o in flagged)
    md += ["", "## Loops", ""] + loop_lines(loops, links, nn, near)
    md += [
        "",
        f"## Unit overlap ({len(flagged)} near-duplicate pairs)",
        "",
        "Near-duplicate pairs were named in the LLM's prompt, and same-run evidence between "
        "them is reported as `expected` rather than as support. The most overlapping pairs:",
        "",
        "| Pair | Article overlap | Source overlap | Near-duplicate |",
        "|---|---|---|---|",
    ]
    uname = {u.uid: u.title for u in units}
    top = sorted(overlaps, key=lambda o: (-o.flagged, -max(o.articles, o.sources)))[:10]
    md += [
        f"| {uname[o.a]} ({o.a}) / {uname[o.b]} ({o.b}) | {o.articles:.2f} | {o.sources:.2f} | "
        f"{'yes' if o.flagged else ''} |"
        for o in top
    ]
    md += ["", "## Significant lagged data signals the LLM did not propose", ""]
    md += [
        f"- {uname[t.a]} ({t.a}) → {uname[t.b]} ({t.b}): r={t.r:+.2f}, lag {t.lag}, q={t.q:.3f}"
        for t in unproposed
    ] or ["- none"]
    return "\n".join(md) + "\n"


def render(cld_path: Path, cld_src: Path) -> None:
    for ext in ("png", "svg"):
        subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; from cld_tool.cli import main; sys.exit(main(sys.argv[1:]))",
                str(cld_path),
                str(cld_path.with_suffix(f".{ext}")),
                "--no-color",
                "--node-shape",
                "oval",
            ],
            check=True,
            env={**os.environ, "PYTHONPATH": str(cld_src)},
        )


# --------------------------------------------------------------------------


async def run_kind(kind: str, d: Data, client: LLMClient, args: argparse.Namespace) -> None:
    out_dir = args.out_dir / kind
    out_dir.mkdir(parents=True, exist_ok=True)
    log: list[str] = [f"as of {d.as_of}: {len(d.runs)} runs"] if d.as_of else []
    warn: list[str] = []
    units = (
        topic_units(d)
        if kind == "topics"
        else community_units(d, log, tuple(args.target_communities))
    )
    valid = {u.uid for u in units}

    measures: dict[str, dict[tuple[str, str, int], Test]] = {
        "share (CLR)": scan(share_clr_series(d, units), partial=False, n_runs=len(d.runs)),
    }
    if kind == "topics":
        measures["urgency (partial)"] = scan(
            urgency_series(d, units), partial=True, n_runs=len(d.runs)
        )
    for m, tests in measures.items():
        sig = [t for t in tests.values() if t.q <= FDR_Q and abs(t.r) >= MIN_R_SUPPORT]
        log.append(
            f"{m}: {len(tests)} tests, {len(sig)} significant "
            f"({sum(t.lag > 0 for t in sig)} at lag>0)"
        )

    overlaps = unit_overlaps(
        d, units, kind == "topics", args.overlap_articles, args.overlap_sources
    )
    flagged = [o for o in overlaps if o.flagged]
    near = frozenset(frozenset((o.a, o.b)) for o in flagged)
    log.append(
        f"{len(flagged)} near-duplicate unit pairs: "
        + (", ".join(f"{o.a}/{o.b}" for o in flagged) or "none")
    )
    prompt = build_prompt(units, d, kind, flagged)
    system = system_prompt(args.max_links)
    (out_dir / "prompt.txt").write_text(system + "\n\n" + prompt)
    print(f"[{kind}] {len(units)} units, prompt ~{len(prompt) // 4} tokens; sampling {args.runs}x")
    results = await asyncio.gather(
        *(
            client.complete_structured(prompt, SystemHypothesis, system=system)
            for _ in range(args.runs)
        ),
        return_exceptions=True,
    )
    samples = []
    for i, res in enumerate(results, 1):
        if isinstance(res, BaseException):
            warn.append(f"sample {i} failed: {type(res).__name__}: {str(res)[:200]}")
            continue
        h, _ = res
        (out_dir / f"sample{i}.json").write_text(h.model_dump_json(indent=2))
        print(f"[{kind}] sample {i}: {len(h.variables)} variables, {len(h.links)} links")
        samples.append(h)
    if len(samples) < args.min_agree:
        print(f"[{kind}] only {len(samples)} samples succeeded; skipping. {warn}")
        return

    links, display = consensus(samples, valid, args.min_agree, warn)
    for lk in links:
        lk.evidence = {
            m: classify_pair(
                t, lk.src, lk.tgt, lk.coverage_sign, frozenset((lk.src, lk.tgt)) in near
            )
            for m, t in measures.items()
        }
        lk.status = combine([s for s, _ in lk.evidence.values()])
    proposed = {(lk.src, lk.tgt) for lk in links}
    unproposed = sorted(
        (
            t
            for tests in measures.values()
            for t in tests.values()
            if t.lag > 0
            and t.q <= FDR_Q
            and abs(t.r) >= MIN_R_SUPPORT
            and (t.a, t.b) not in proposed
        ),
        key=lambda t: -abs(t.r),
    )
    log.append(
        f"consensus: {len(links)} links from samples of "
        + "/".join(str(len(s.links)) for s in samples)
        + " proposed links"
    )
    log += warn

    nn = node_names(links, display)
    loops = loops_of(links, nn, args.max_loops, args.max_loop_length)
    cld_path = out_dir / "causal_hypothesis.cld"
    title = f"Causal Loop Hypothesis: {kind}" + (f" (as of {d.as_of})" if d.as_of else "")
    cld_path.write_text(to_cld(links, nn, title))
    (out_dir / "causal_hypothesis.md").write_text(
        to_markdown(
            kind,
            units,
            links,
            nn,
            loops,
            list(measures),
            unproposed,
            log,
            len(samples),
            args.min_agree,
            args.model,
            overlaps,
        )
    )
    (out_dir / "units.json").write_text(
        json.dumps(
            [
                {"id": u.uid, "title": u.title, "top_tags": u.top_tags, "articles": len(u.articles)}
                for u in units
            ],
            indent=1,
        )
    )
    # Read back by cld_rating.py, which must rebuild these same units and links.
    config = {
        k: getattr(args, k)
        for k in (
            "model",
            "runs",
            "min_agree",
            "temperature",
            "target_communities",
            "max_links",
            "max_loops",
            "max_loop_length",
            "as_of",
            "overlap_articles",
            "overlap_sources",
        )
    }
    (out_dir / "config.json").write_text(json.dumps(config, indent=1))
    render(cld_path, args.cld_src)
    print(f"[{kind}] " + "; ".join(log))
    print(f"[{kind}] status: {dict(Counter(lk.status for lk in links))}; loops: {len(loops)}")


async def main() -> None:
    global MAX_LINKS
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--units", nargs="+", choices=["topics", "communities"], default=["topics", "communities"]
    )
    ap.add_argument("--model", default=os.environ.get("LLM_MODEL", "ollama_chat/glm-5.2:cloud"))
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--min-agree", type=int, default=2)
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="Where to write per-unit-set outputs (one subdirectory each).",
    )
    ap.add_argument(
        "--cld-src",
        type=Path,
        default=Path(os.environ.get("CLD_TOOL_SRC", DEFAULT_CLD_SRC)),
        help="causal-loop-diagram-inator's src/ dir (env: CLD_TOOL_SRC).",
    )
    ap.add_argument(
        "--target-communities",
        type=int,
        nargs=2,
        metavar=("LO", "HI"),
        default=list(TARGET_COMMUNITIES),
        help="Community count to aim for in the Louvain resolution sweep (more = more nodes).",
    )
    ap.add_argument(
        "--max-links",
        type=int,
        default=MAX_LINKS,
        help="Most links one sample may propose (enforced by the response model).",
    )
    ap.add_argument(
        "--max-loops", type=int, default=MAX_LOOPS, help="Most loops declared in the diagram."
    )
    ap.add_argument(
        "--max-loop-length",
        type=int,
        default=MAX_LOOP_LENGTH,
        help="Longest loop (in nodes) looked for.",
    )
    ap.add_argument(
        "--overlap-articles",
        type=float,
        default=OVERLAP_ARTICLES,
        help="Article-overlap (Jaccard) at which two units count as near-duplicates.",
    )
    ap.add_argument(
        "--overlap-sources",
        type=float,
        default=OVERLAP_SOURCES,
        help="Source-domain overlap at which two topics count as near-duplicates (topics only).",
    )
    ap.add_argument(
        "--as-of",
        default=None,
        help="Draft from the archive as it stood at a point in time: a run_id (that run and "
        "everything before it), an ISO timestamp (naive = UTC), or a YYYY-MM-DD date (through "
        "the end of that UTC day). Default: every run.",
    )
    args = ap.parse_args()
    if args.target_communities[0] > args.target_communities[1]:
        ap.error("--target-communities LO must be <= HI")
    MAX_LINKS = args.max_links
    d = load(os.environ.get("DAILY_REPORT_DATABASE_URL") or os.environ["DATABASE_URL"], args.as_of)
    print(
        f"{len(d.runs)} runs, {len(d.article_topic)} articles, {len(d.bad_runs)} runs w/o archive"
        + (f" (as of {d.as_of})" if d.as_of else "")
    )
    runs_with_articles = len(set(d.article_run.values()))
    if runs_with_articles == 0:
        sys.exit("no runs with archived articles in range; nothing to draft from")
    if runs_with_articles < MIN_RUNS_FOR_LAG + MAX_LAG:
        print(
            f"WARNING: only {runs_with_articles} runs with articles; correlations need "
            f"{MIN_RUNS_FOR_LAG} aligned pairs, so links will mostly come back not_detected"
        )
    client = LLMClient(
        model=args.model, temperature=args.temperature, instructor_mode=instructor.Mode.JSON
    )
    await asyncio.gather(*(run_kind(k, d, client, args) for k in args.units))


if __name__ == "__main__":
    asyncio.run(main())
