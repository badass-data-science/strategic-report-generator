# Causal loop diagram prototype

Exploratory, not part of the pipeline: nothing here is called from `cli.py`
or `flows/daily_report.py`, so there's no entry-point parity to maintain
yet, and nothing is persisted to the tracking database. The goal is to judge
whether a causal loop diagram (CLD), rendered with the separate
`causal-loop-diagram-inator` project (`cld`), could usefully accompany a
day's strategic report.

## The idea

Draw a CLD whose nodes are **units**: either the user-defined topics or
communities from a **frozen** Louvain partition of the cumulative tag graph.
The unit set is pluggable on purpose, so the topics *can* be the defined
ones but don't have to be. The process is **hypothesis first, data second**:

1. **Units and series.** For each unit and run, compute its share of that
   run's articles, centered-log-ratio (CLR) transformed so that "shares sum
   to 1" doesn't manufacture negative correlations. For topics, also the
   urgency score, partial-correlated against the leave-one-out mean urgency
   (same control as `systems_signals.py`).
2. **Blind proposal.** An LLM drafts a CLD among the units from qualitative
   context only (top tags, latest strategic bullets, closest recent daily
   community summaries, headlines, cross-topic overview, bridge tags). It
   never sees the correlations, so the check in step 4 is independent. Each
   variable declares a `direction` relative to its unit's coverage, so a
   variable like "Market Stability" on a market-turmoil unit flips the
   expected sign correctly.
3. **Agreement across 3 samples.** Draw 3 samples at temperature 0.7 and keep
   a link only if at least 2 agree on its source, target and
   coverage-level sign. This filters out one-off inventions. It measures
   stability, not truth: samples can share a bias.
4. **Data check.** Each kept link is tested at lags 0 to 3 (counted in runs)
   with BH-FDR across the whole scan, and labelled supported, weak, mixed,
   contradicted or not detected. Edges are coloured by that label in the
   rendered diagram (`+`/`-` labels carry polarity).

### Why not tag-level Systems Signals directly

That was tried first (scratchpad only, not committed). On 2026-10-13 (31
runs) there were 147 tag-rate signals but only 26 directed (lag > 0) pairs
and a single directed cycle, all positive, so every loop would be classified
reinforcing. An LLM filter rejected about 60% of them as coverage artifacts
(same publisher or beat, same entity), and its added links ended up closing
the only longer loop. The rejection step was the most reliable part and
might be worth reusing as an artifact filter for Systems Signals on its
own.

### Why a frozen partition, not the daily communities

Daily Louvain communities can't be tracked across runs: a community's best
match in the previous run shares a median of 0.11 of its tags (Jaccard),
and none share half. A partition of the *cumulative* graph is stable:
about 0.9 best-match Jaccard across seeds, and about 0.7 against a partition
built from only the first two-thirds of history, so it drifts and would need
versioning. A resolution sweep picks about 15 to 20 communities (the default
resolution gives only 8).

## Results as of 2026-10-13 (31 runs, `glm-5.2:cloud`)

| | Topics (17) | Communities (15, resolution 1.5) |
|---|---|---|
| Consensus links | 19 | 20 |
| Loops | 4 (1 balancing) | 8 (1 balancing) |
| Supported / contradicted / weak / not detected | 8 / 7 / 2 / 2 | 0 / 0 / 3 / 17 |
| Significant tests (share) | 142, of which 2 lagged | 8, none lagged |
| Significant tests (urgency) | 0 | n/a |

- The diagrams read like real CLDs: measurable variables, several loops,
  balancing feedback. The community version is the more coherent economic
  model.
- **No proposed link had lagged evidence.** Every supported or contradicted
  verdict came from a lag-0 share correlation, which has no direction.
  Lag-0 results should be read as co-movement, not support.
- Same-day share co-movement is not explained by Monday runs (the archive
  has no weekend runs) or total article volume (all |r| < 0.5). Shared
  feeds explain part of it (AI and Data Science share 2 feeds and 5 domains).
- The data did surface one link the LLM missed: Defense coverage leads
  Geopolitics by 3 runs (r = +0.63, q = 0.005). The LLM proposed the reverse
  direction, which the data rates only weak.

Bottom line: hypothesis-first works as a diagram generator, but article
share measures publishing volume, not the world, and after 31 runs it can't
confirm or refute causal links.

## Variable rating: measure the diagram's variables directly (tried, negative)

`cld_rating.py` takes the consensus diagrams drafted by `cld_units.py` and,
for every run, asks an LLM (temperature 0.1) to rate each diagram variable's
current real-world movement from -2 to +2, or null for no information, from
that run's articles for the variable's unit (up to 20 articles, title plus
first summary bullet). The rater never sees the links. Each link is then
tested on the rating series. The verdict uses lags 1 to 3 only, with FDR over
the pre-specified links times lags (legitimate because the links were
proposed blind). Lag 0 is shown as context only, since one article mentioning
two variables inflates it.

Results as of 2026-10-13 (30 rated runs, about 60 rating calls):

- **No lagged support.** 1 weak link per unit set; nothing survives FDR; a
  full lagged scan finds no unproposed relationships either. With about 27
  points per test, a link needs roughly |r| >= 0.6. The largest lagged
  correlations were 0.4 to 0.6.
- **Growth-framed variables come back "rising" every day.** Topic variables
  like "AI Agent Deployment Scale" (+2 in 17 of 20 rated runs), "Data Quality
  Investment" (+1 in 13 of 13, SD 0.00) and "Crypto Institutional Adoption"
  (SD 0.20) have almost no variance, so they can't correlate with anything.
  Most topic-link failures are "can't test", not "data says no".
- **Market-like community variables move both ways**: Energy Commodity
  Prices (SD 1.03), Crypto Market Volatility (1.13), Market Sentiment (0.80),
  Retail Business Confidence (0.62), Construction Activity (0.71). That's the
  kind of variable this method can work with.
- **Lag 0 shows co-movement**: AI Infrastructure with Semiconductor Demand
  (r = +0.51) and with Manufacturing Automation (+0.56), q = 0.019. That's
  either a shared news cycle or the rater reading one upbeat batch into
  several variables. It isn't directional either way.
- **Timescale mismatch.** A lag of 1 to 3 runs is 1 to 3 business days; most
  proposed mechanisms (inflation to clean-energy manufacturing, AI deployment
  to governance maturity) act over months.

Ideas if this route is resumed: rate level ("how intense today, 1-5")
instead of direction; ask the proposer for fast-moving, rateable variables
(prices, sentiment, threat levels, policy actions), keeping slow trends as
structure labelled untestable at daily resolution; and first check that the
rater tracks ground truth on market variables (daily oil, BTC, S&P/VIX,
dollar index, 10-year yield). That validation is the next step being
prototyped.

## Running

```bash
source ~/environment_variables/strategic_daily_report.sh   # DAILY_REPORT_DATABASE_URL
.venv/bin/python prototypes/causal_loops/cld_units.py --out-dir /tmp/cld_out
```

`--units topics communities` (default both), `--runs 3 --min-agree 2
--temperature 0.7`, `--model` (default `LLM_MODEL` or
`ollama_chat/glm-5.2:cloud`, JSON instructor mode), and `--cld-src` (or
`CLD_TOOL_SRC`) pointing at causal-loop-diagram-inator's `src/`.

Size of the diagram:

| Flag | Default | Effect |
|---|---|---|
| `--min-agree` | 2 | `1` keeps every link any sample proposed (the union); the biggest lever for more links and loops, at the cost of one-off inventions. |
| `--runs` | 3 | More samples give links more chances to reach `--min-agree`. |
| `--temperature` | 0.7 | Higher = more varied samples: more links under a low `--min-agree`, fewer under a high one. |
| `--target-communities LO HI` | 15 20 | Community count the resolution sweep aims for (closest wins if none lands in range). On 2026-10-13 it reached 25 at resolution 2.0 and 48 at 4.0. Check the logged seed-stability: 0.90 at 15, 0.73 at 25, 0.93 at 48. |
| `--max-links` | 22 | Most links one sample may propose; enforced by the response model, so instructor retries an over-long answer. |
| `--max-loops` | 10 | Most loops declared in the diagram (shortest first). |
| `--max-loop-length` | 6 | Longest loop, in nodes, that's looked for. |

For example, a deliberately loose diagram:

```bash
.venv/bin/python prototypes/causal_loops/cld_units.py --out-dir /tmp/cld_loose \
    --units communities --target-communities 25 30 --max-links 35 \
    --runs 5 --min-agree 1 --max-loops 20
```

Each unit set's settings are saved to `<out-dir>/<kind>/config.json`, and
`cld_rating.py` reads them back so it rebuilds the same units and links. Rendering
also needs the `d2` and `inkscape` CLIs. It reads the tracking database only;
each run makes 3 LLM calls per unit set (about 10k prompt tokens each).

```bash
.venv/bin/python prototypes/causal_loops/cld_rating.py \
    --cld-dir /tmp/cld_out --out-dir /tmp/rating_out [--last-n 2]
```

Needs `cld_units.py`'s output for the same database state (it aborts if the
units have changed since drafting, e.g. after new runs). One LLM call per
run per unit set; ratings are cached per run, so it can be resumed.
