"""
Prototype (not wired into the pipeline): rate each CLD variable directly every run, then test
the CLD's links on those rating series instead of on article share.

Reuses the consensus CLDs already drafted by cld_units.py (their
sample*.json and units.json under --cld-dir/<kind>/), so the variables are
fixed before any rating happens. The rater never sees the links. Ratings are
cached per run under --out-dir/<kind>/ratings/, so a backfill can resume.

    source ~/environment_variables/strategic_daily_report.sh
    .venv/bin/python prototypes/causal_loops/cld_rating.py \
        --cld-dir /tmp/cld_out --out-dir /tmp/rating_out
"""

import argparse
import asyncio
import json
import os
import statistics
import sys
from collections import Counter
from itertools import permutations
from pathlib import Path
from typing import Any

import instructor
from pydantic import BaseModel, Field, model_validator

sys.path.insert(0, str(Path(__file__).parent))

import cld_units as cu  # noqa: E402
from strategic_reports.daily.core.db import get_connection  # noqa: E402
from strategic_reports.daily.core.llm_client import LLMClient  # noqa: E402
from strategic_reports.daily.core.systems_signals import (  # noqa: E402
    _benjamini_hochberg_qvalues,
    _pearson_p_value,
    lagged_pearson,
)

ARTICLES_PER_VARIABLE = 20
CONCURRENCY = 4


# --------------------------------------------------------------------------
# Rating schema
# --------------------------------------------------------------------------


class VariableRating(BaseModel):
    variable_id: str
    movement: int | None = Field(
        default=None,
        ge=-2,
        le=2,
        description="How these articles indicate the variable is moving in the real world "
        "right now: -2 falling sharply, -1 falling, 0 flat or mixed, +1 rising, +2 rising "
        "sharply. null if the articles carry no information about this variable.",
    )
    evidence: str = Field(
        description="One short sentence citing what in the articles "
        "supports the rating (or why there is no information)."
    )


class RunRatings(BaseModel):
    ratings: list[VariableRating]

    @model_validator(mode="after")
    def _unique(self) -> "RunRatings":
        ids = [r.variable_id for r in self.ratings]
        if len(ids) != len(set(ids)):
            raise ValueError("rate each variable_id exactly once")
        return self


SYSTEM = """\
You rate how real-world quantities are moving, based only on one day's news \
articles. For each variable you get a definition and that day's relevant \
articles. Judge what the articles report about the variable's CURRENT \
direction in the world (not how much it is being written about). Use null \
when the articles say nothing usable about it -- that is expected and fine; \
do not guess. Rate each variable independently of the others.
"""


def build_rating_prompt(
    run_id: str,
    variables: list[tuple[str, str, cu.Unit]],
    run_articles: dict[str, list[str]],
) -> str:
    lines = [f"Run {run_id}. Rate every variable below exactly once."]
    for uid, name, unit in variables:
        lines.append(f"\n## {uid}: {name}")
        lines.append(f"Unit context: {unit.title}; tags: {', '.join(unit.top_tags[:8])}")
        arts = run_articles.get(uid, [])
        if not arts:
            lines.append("(no articles this run)")
        lines += [f"- {a}" for a in arts]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Rebuild the drafted CLD
# --------------------------------------------------------------------------


def load_config(cld_dir: Path, kind: str) -> dict[str, Any]:
    """cld_units.py's settings for this draft; defaults for drafts made before config.json."""
    config: dict[str, Any] = {
        "min_agree": 2,
        "target_communities": list(cu.TARGET_COMMUNITIES),
        "max_links": cu.MAX_LINKS,
        "max_loops": cu.MAX_LOOPS,
        "max_loop_length": cu.MAX_LOOP_LENGTH,
    }
    path = cld_dir / kind / "config.json"
    if path.exists():
        config.update(json.loads(path.read_text()))
    return config


def rebuild_cld(
    kind: str, d: cu.Data, cld_dir: Path, config: dict[str, Any]
) -> tuple[list[cu.Unit], list[cu.CLink], dict[str, tuple[str, str]]]:
    log: list[str] = []
    units = (
        cu.topic_units(d)
        if kind == "topics"
        else cu.community_units(d, log, tuple(config["target_communities"]))
    )
    saved = json.loads((cld_dir / kind / "units.json").read_text())
    now = [{"id": u.uid, "title": u.title} for u in units]
    if [{"id": s["id"], "title": s["title"]} for s in saved] != now:
        sys.exit(f"[{kind}] units changed since the CLD was drafted (new runs?); redraft first")
    samples = [
        cu.SystemHypothesis.model_validate_json(p.read_text())
        for p in sorted((cld_dir / kind).glob("sample*.json"))
    ]
    links, display = cu.consensus(samples, {u.uid for u in units}, config["min_agree"], [])
    return units, links, display


def load_first_bullets(database_url: str) -> dict[int, str]:
    with get_connection(database_url) as c:
        rows = c.execute(
            "SELECT article_id, bullet_text FROM article_summary_bullets WHERE bullet_index = 0"
        ).fetchall()
    return dict(rows)


def articles_for_run(
    d: cu.Data, units: dict[str, cu.Unit], run_id: str, bullets: dict[int, str]
) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for uid, u in units.items():
        arts = [a for a in u.articles if d.article_run[a] == run_id]
        if not u.is_topic:
            tagset = set(u.top_tags)
            arts.sort(key=lambda a: (-len(d.article_tags[a] & tagset), a))
        else:
            arts.sort()
        out[uid] = [
            d.article_title[a] + (f" -- {bullets[a]}" if a in bullets else "")
            for a in arts[:ARTICLES_PER_VARIABLE]
        ]
    return out


# --------------------------------------------------------------------------
# Rating (cached per run)
# --------------------------------------------------------------------------


async def rate_all(
    kind: str,
    d: cu.Data,
    units: list[cu.Unit],
    links: list[cu.CLink],
    display: dict[str, tuple[str, str]],
    bullets: dict[int, str],
    client: LLMClient,
    cache: Path,
    last_n: int | None = None,
) -> dict[str, dict[str, VariableRating]]:
    cache.mkdir(parents=True, exist_ok=True)
    by_id = {u.uid: u for u in units}
    used = sorted({u for lk in links for u in (lk.src, lk.tgt)}, key=lambda x: int(x[1:]))
    variables = [(uid, display[uid][0], by_id[uid]) for uid in used]
    sem = asyncio.Semaphore(CONCURRENCY)
    results: dict[str, dict[str, VariableRating]] = {}

    async def one(run_id: str) -> None:
        path = cache / f"{run_id}.json"
        if path.exists():
            rr = RunRatings.model_validate_json(path.read_text())
        else:
            arts = articles_for_run(d, {uid: by_id[uid] for uid in used}, run_id, bullets)
            if not any(arts.values()):
                return
            prompt = build_rating_prompt(run_id, variables, arts)
            async with sem:
                try:
                    rr, _ = await client.complete_structured(prompt, RunRatings, system=SYSTEM)
                except Exception as e:  # one bad run must not sink the backfill
                    print(f"[{kind}] {run_id} failed: {type(e).__name__}: {str(e)[:150]}")
                    return
            path.write_text(rr.model_dump_json(indent=1))
            print(f"[{kind}] rated {run_id}")
        results[run_id] = {r.variable_id: r for r in rr.ratings if r.variable_id in by_id}

    todo = [r for r in d.runs if r not in d.bad_runs]
    await asyncio.gather(*(one(r) for r in (todo[-last_n:] if last_n else todo)))
    return results


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------


def rating_series(
    runs: list[str], ratings: dict[str, dict[str, VariableRating]], uids: list[str]
) -> cu.Series:
    s: cu.Series = {u: [None] * len(runs) for u in uids}
    for i, r in enumerate(runs):
        for u in uids:
            vr = ratings.get(r, {}).get(u)
            if vr is not None and vr.movement is not None:
                s[u][i] = float(vr.movement)
    return s


def tests_for(
    series: cu.Series, pairs: list[tuple[str, str]], lags: range
) -> dict[tuple[str, str, int], cu.Test]:
    tests = []
    for a, b in pairs:
        for lag in lags:
            out = lagged_pearson(series[a], series[b], lag)
            if out is not None:
                r, n = out
                tests.append(cu.Test(a, b, lag, r, n, _pearson_p_value(r, n)))
    for t, q in zip(tests, _benjamini_hochberg_qvalues([t.p for t in tests]), strict=True):
        t.q = q
    return {(t.a, t.b, t.lag): t for t in tests}


def autocorr(xs: list[float | None]) -> float | None:
    out = lagged_pearson(xs, xs, 1)
    return out[0] if out else None


def analyse(
    kind: str,
    d: cu.Data,
    units: list[cu.Unit],
    links: list[cu.CLink],
    display: dict[str, tuple[str, str]],
    ratings: dict[str, dict[str, VariableRating]],
    out_dir: Path,
    cld_src: Path,
    config: dict[str, Any],
) -> None:
    uids = sorted({u for lk in links for u in (lk.src, lk.tgt)}, key=lambda x: int(x[1:]))
    s = rating_series(d.runs, ratings, uids)
    pairs = [(lk.src, lk.tgt) for lk in links]
    lagged = tests_for(s, pairs, range(1, cu.MAX_LAG + 1))
    same_run = tests_for(s, pairs, range(0, 1))
    full = tests_for(s, list(permutations(uids, 2)), range(1, cu.MAX_LAG + 1))

    for lk in links:
        sign = 1 if lk.polarity == "+" else -1
        lk.evidence = {
            "rating, lag 1-3": cu.classify(lagged, lk.src, lk.tgt, sign),
            "rating, lag 0 (context)": cu.classify(same_run, lk.src, lk.tgt, sign),
        }
        lk.status = lk.evidence["rating, lag 1-3"][0]

    rated_runs = len(ratings)
    md = [
        f"# Variable-rating check: {kind} (prototype)",
        "",
        f"{rated_runs} runs rated, {len(uids)} variables, {len(links)} links. Verdict uses "
        "lags 1-3 only (FDR over the pre-specified links x lags); lag 0 shown as context "
        "since one article mentioning both variables inflates it.",
        "",
        "## Variables",
        "",
        "| Id | Variable | Rated runs | Mean | SD | Lag-1 autocorr | Ratings |",
        "|---|---|---|---|---|---|---|",
    ]
    for u in uids:
        vals = [v for v in s[u] if v is not None]
        dist = Counter(int(v) for v in vals)
        ac = autocorr(s[u])
        md.append(
            f"| {u} | {display[u][0]} | {len(vals)}/{rated_runs} | "
            f"{statistics.mean(vals):+.2f} | {statistics.pstdev(vals):.2f} | "
            f"{'—' if ac is None else f'{ac:+.2f}'} | "
            + " ".join(f"{k:+d}:{dist[k]}" for k in range(-2, 3))
            + " |"
            if vals
            else f"| {u} | {display[u][0]} | 0/{rated_runs} | — | — | — | — |"
        )
    counts = Counter(lk.status for lk in links)
    md += [
        "",
        "## Links (" + ", ".join(f"{k} {v}" for k, v in counts.most_common()) + ")",
        "",
        "| Link | Pol | Status (lag 1-3) | Lag 1-3 | Lag 0 (context) | Mechanism |",
        "|---|---|---|---|---|---|",
    ]
    order = list(cu.STATUS_COLORS)
    for lk in sorted(links, key=lambda lk: order.index(lk.status)):
        ev = " | ".join(cu.fmt_ev(*v) for v in lk.evidence.values())
        md.append(
            f"| {display[lk.src][0]} → {display[lk.tgt][0]} | {lk.polarity} | "
            f"**{lk.status}** | {ev} | {lk.mechanism} |"
        )
    unproposed = sorted(
        (
            t
            for t in full.values()
            if t.q <= cu.FDR_Q and abs(t.r) >= cu.MIN_R_SUPPORT and (t.a, t.b) not in set(pairs)
        ),
        key=lambda t: -abs(t.r),
    )
    nn = cu.node_names(links, display)
    loops = cu.loops_of(links, nn, config["max_loops"], config["max_loop_length"])
    md += ["", "## Loops (edge evidence = lag 1-3 verdicts)", ""] + cu.loop_lines(loops, links, nn)
    md += [
        "",
        "## Lagged relationships between rated variables the CLD didn't propose "
        f"(full scan, {len(full)} tests)",
        "",
    ]
    md += [
        f"- {display[t.a][0]} → {display[t.b][0]}: r={t.r:+.2f}, lag {t.lag}, n={t.n}, q={t.q:.3f}"
        for t in unproposed
    ] or ["- none"]
    md += ["", "## Spot check: latest run's ratings", ""]
    last = max(ratings, key=d.runs.index)
    for u in uids:
        vr = ratings[last].get(u)
        if vr:
            mv = "null" if vr.movement is None else f"{vr.movement:+d}"
            md.append(f"- **{display[u][0]}** {mv}: {vr.evidence}")

    out_dir.mkdir(parents=True, exist_ok=True)
    cld_path = out_dir / "rated_hypothesis.cld"
    cld_path.write_text(
        cu.to_cld(
            links,
            nn,
            f"Causal Loop Hypothesis: {kind}, checked on rated variables (lag 1-3)",
        )
    )
    (out_dir / "rated_hypothesis.md").write_text("\n".join(md) + "\n")
    cu.render(cld_path, cld_src)
    nulls = sum(v is None for u in uids for v in s[u])
    print(
        f"[{kind}] {rated_runs} runs; null/missing ratings {nulls}/{len(uids) * len(d.runs)}; "
        f"lagged verdicts {dict(counts)}; unproposed lagged {len(unproposed)}"
    )


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--units", nargs="+", default=["topics", "communities"])
    ap.add_argument(
        "--cld-dir", type=Path, required=True, help="cld_units.py --out-dir from the drafting run"
    )
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--model", default=os.environ.get("LLM_MODEL", "ollama_chat/glm-5.2:cloud"))
    ap.add_argument(
        "--min-agree",
        type=int,
        default=None,
        help="Override the draft's own --min-agree (read from its config.json).",
    )
    ap.add_argument("--last-n", type=int, default=None, help="Only rate the last N runs.")
    ap.add_argument("--cld-src", type=Path, default=cu.DEFAULT_CLD_SRC)
    args = ap.parse_args()
    url = os.environ.get("DAILY_REPORT_DATABASE_URL") or os.environ["DATABASE_URL"]
    d = cu.load(url)
    bullets = load_first_bullets(url)
    client = LLMClient(model=args.model, temperature=0.1, instructor_mode=instructor.Mode.JSON)

    async def kind_task(kind: str) -> None:
        config = load_config(args.cld_dir, kind)
        if args.min_agree is not None:
            config["min_agree"] = args.min_agree
        cu.MAX_LINKS = config["max_links"]  # so the draft's samples re-validate
        units, links, display = rebuild_cld(kind, d, args.cld_dir, config)
        print(
            f"[{kind}] {len(links)} links, "
            f"{len({u for lk in links for u in (lk.src, lk.tgt)})} variables to rate"
        )
        ratings = await rate_all(
            kind,
            d,
            units,
            links,
            display,
            bullets,
            client,
            args.out_dir / kind / "ratings",
            args.last_n,
        )
        analyse(kind, d, units, links, display, ratings, args.out_dir / kind, args.cld_src, config)

    await asyncio.gather(*(kind_task(k) for k in args.units))


if __name__ == "__main__":
    asyncio.run(main())
