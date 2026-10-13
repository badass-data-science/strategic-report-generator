# Drawing the Loops (Our Heroine Hands a Robot a Pencil, Then Grades Its Homework)

### How a causal loop diagram gets drafted from a news archive, and how the archive gets a vote on whether it's true

Faithful readers of "Everything Correlates With Everything" will remember how that dispatch ended: with a list of things the Ultimate Cunning Master Plan™ conspicuously did not do yet. Near the top of that list sat a small confession labeled **No higher-order loop detection**. Everything Systems Signals found was strictly pairwise (does A predict B), when a real causal loop diagram usually wants three or more variables chasing each other around a circle: A drives B, B drives C, and C comes back around to bother A again.

Our heroine does not like leaving confessions unresolved. Unresolved confessions are how henchmen get ideas. So this is the story of `cld_units.py`, a prototype that drafts a causal loop diagram of her empire's news, checks that diagram against the archive's own time series, and renders the result as an actual picture, along with a fairly candid account of how much of that picture the data is currently willing to vouch for. (Spoiler: less than she'd like. This is becoming something of a house style.)

## A Causal Loop Diagram, Briefly, for Henchmen Who Skipped the Off-Site

A causal loop diagram (CLD) is a drawing with three ingredients. **Variables** are quantities that can go up or down: "Inflation Pressure," "Semiconductor Demand," "Henchman Morale." **Links** are arrows between variables, each marked with a polarity: `+` means the target moves in the *same* direction as the source (more AI infrastructure spending, more chip demand), and `-` means it moves in the *opposite* direction (more energy-price pain, less money left over for factory automation). A link can also be marked as *delayed*, drawn as two little hash marks across the arrow, when its effect takes a while to arrive.

The third ingredient is the reason anyone bothers: **loops**. Follow the arrows around a closed cycle and multiply the polarities together. An even number of `-` links makes a *reinforcing* loop (R), which amplifies whatever it's given, for better or worse; an odd number makes a *balancing* loop (B), which pushes back against change. Real systems have both, and the balancing loops are usually the ones that surprise people, because they're the reason the obvious plan never works quite as well as the slide deck promised.

The drawing itself is handled by a separate project in the empire's arsenal, the *causal-loop-diagram-inator*, which compiles a tiny text format into a D2 diagram, then decorates the rendered SVG with polarity labels and delay marks placed beside each curve. `cld_units.py` never draws anything itself. Its whole job is deciding what to tell the inator.

## Attempt One: Just Draw the Systems Signals (It Did Not Go Well)

The obvious first move was to take Systems Signals' own output and draw it. By 2026-10-13 the archive had grown from the fourteen runs of the previous dispatch to thirty-one, and the tag-level side of Systems Signals had gone from reporting zero candidates to reporting 147. That sounds like a lot of arrows. It was not a lot of arrows.

Of those 147, 120 were lag-zero pairs: two tags moving together in the same run, which, as the previous dispatch spent several thousand words insisting, says nothing about which one is driving the other. The remaining 27 directed signals collapsed to 26 distinct pairs, scattered across about twenty disconnected fragments, containing exactly one closed cycle ("Momentum" and "Analyst," leading each other in both directions). Every single signal was positive, which meant every loop the diagram could ever contain would be classified reinforcing, which meant the R/B classification would carry precisely zero information. A diagram in which every loop is reinforcing isn't a model of a system. It's a model of optimism.

Our heroine tried rescuing it by handing those 26 signals to an LLM, along with each tag's recent headlines and community summary, and asking it to keep only the links with a plausible real-world mechanism. The LLM turned out to be excellent at the *rejecting* half of that job: "Bollinger Band" leading "Rally" is a crypto price-prediction article template, "Stock Rating" leading "MarketBeat" is just the publisher of the stock ratings, and "Watermark" leading "SynthID" is one product by two names. Sixteen of twenty-six signals went in the bin, with grounded reasons. But the only longer loop in the resulting diagram was closed by links the LLM had invented itself, which is a polite way of saying the robot had drawn the interesting part freehand.

The lesson was about granularity. Tags are the wrong unit for a diagram meant to accompany a strategic report: too many of them, too thin per tag, and too prone to being two names for one thing. The nodes needed to be bigger.

## Choosing the Nodes: Topics, or Communities That Hold Still

`cld_units.py` works on *units*, and a unit can be either of two things. The first is one of the seventeen topics our heroine defined months ago (Defense, Energy, Forex, Genomics, and so on), which have the advantage of already existing and the disadvantage of having been invented in a Slack conversation with her henchmen rather than discovered in the data.

The second option is a Louvain community, which is where things got interesting, because the obvious version doesn't work. The pipeline already runs Louvain on every night's tag graph, but it re-partitions from scratch each time, so "community 4" on Tuesday has nothing to do with "community 4" on Wednesday. Our heroine measured how badly: for each community, she found its best match in the previous run by Jaccard similarity, the overlap between two sets divided by their union:

```
J(A, B) = |A ∩ B| / |A ∪ B|
```

The median best match was 0.11, and not a single community shared even half its tags with anything from the night before. You cannot build a time series out of something that changes its identity every night. That's not a variable. That's a witness protection program.

The fix is to stop letting the communities move. `cld_units.py` sums every run's tag co-occurrence edges into one *cumulative* graph, discards the weak material (edges with total weight below 5, tags appearing fewer than 10 times in the whole archive), and runs Louvain once, over the entire history. Louvain works by greedily maximizing *modularity*, which measures how much denser the connections inside communities are than you'd expect by chance:

```
Q = (1 / 2m) · Σ_ij [ A_ij − γ · k_i · k_j / (2m) ] · δ(c_i, c_j)
```

Here `A_ij` is the edge weight between tags `i` and `j`, `k_i` is a tag's total edge weight, `m` is the total weight in the graph, and `δ(c_i, c_j)` is 1 when two tags share a community and 0 otherwise. The `γ` term is the *resolution*: at `γ = 1` you get the classic definition, and raising it penalizes big communities more heavily, so the graph splits into more, smaller ones. At the default resolution the cumulative graph produced only eight communities, the largest of which swallowed 745 tags under the general heading of "artificial intelligence and everything near it," which is coarser than the seventeen hand-made topics and therefore not much of an upgrade.

So `cld_units.py` sweeps the resolution upward (1.0, 1.5, 2.0, and so on) until the number of communities with at least five tags lands in a target range, fifteen to twenty by default. On this archive that happened at `γ = 1.5`, which produced fifteen communities with recognizable personalities: semiconductors, energy and commodities, real estate and construction, crypto and technical analysis, retail and brands, and, inexplicably but consistently, a community devoted to online casinos.

Because a partition is only useful if it's reproducible, the script also re-runs Louvain with a different random seed and reports the median best-match Jaccard between the two partitions. At fifteen communities that came out to 0.90, which is the kind of number our heroine can build on. (In an earlier check at the default resolution, a partition built from only the first two-thirds of the archive matched the full-history one at about 0.71, so partitions do drift as history accumulates. It would need versioning, not daily rebuilding, if this ever graduated from prototype.)

## Turning Units Into Time Series, and Why Shares Lie

Each unit then needs a number per run. The simplest is its *share* of that run's articles: what fraction of tonight's news was about Energy, or touched the semiconductor community. Simple, and subtly treacherous, because shares are compositional: for topics, where each article has exactly one, they add up to one. If Defense has a huge night, every other topic's share shrinks a little, whether or not anything happened to them. Correlate raw shares and you will discover a vast web of negative relationships that exist purely because a pie only has so many slices.

The standard fix for compositional data is the *centered log-ratio* (CLR) transform. For each run, take the log of each unit's share, then subtract the average log share across all units that run:

```
clr(x_k) = log( (count_k + 0.5) / n ) − (1/D) · Σ_j log( (count_j + 0.5) / n )
```

where `count_k` is the number of articles in unit `k` that run, `n` is the run's total article count, and `D` is the number of units. The `+ 0.5` is a pseudo-count, so that a unit with zero articles on a given night gets a large negative value instead of a logarithm of zero, which is the sort of thing that makes a statistics library quietly ruin your evening. After the transform, each unit's value means "how much more or less than the typical unit was this unit covered tonight," which is a question that can be answered for every unit at once without the pie-slice problem. (CLR values still sum to zero, so a faint negative bias of roughly `−1/(D−1)` between units remains, about −0.06 with seventeen units, which is small enough to note and live with. Community shares, strictly speaking, don't add up to one at all, since most articles belong to more than one community, so for communities the CLR is a useful approximation rather than the textbook case.)

For topics specifically there's a second series available: the LLM-scored urgency each topic receives every run, the same series Systems Signals uses. Urgency doesn't add up to anything, so it isn't compositional, but it has the "busy news day" confound the previous dispatch caught red-handed with Energy and Forex. So it gets the same treatment Systems Signals now applies: before correlating two topics, each one is residualized against the leave-one-out mean urgency of every *other* topic that run, using ordinary least squares, and the correlation is computed on what's left over. Two topics riding the same busy day get that shared wave subtracted out before anyone compares them.

## Twins in Disguise: Spotting Near-Duplicate Units

The previous dispatch's list of confessions included **No semantic similarity filtering**: pairs like "fighter aircraft" and "weapon system" survived every one of Systems Signals' filters despite reading, to any human, as near-definitional. Our heroine's first instinct was to bolt the same missing filter onto this prototype. Her second instinct, which is the one that usually saves the empire money, was to measure whether it would help first.

It mostly wouldn't, for a structural reason. Systems Signals correlates *tags* against each other, so two near-synonyms look like a relationship. `cld_units.py` never correlates tags at all. It correlates *units*, and near-synonymous tags overwhelmingly land in the same Louvain community anyway, since they show up in the same articles. The synonym problem is mostly absorbed before the diagram ever sees it.

At the unit level, the thing that actually inflates correlations isn't shared meaning. It's shared *articles*. An article joins every community that any of its tags belongs to, and about 80% of the archive's articles belong to two or more communities. So our heroine measured every community pair's *article overlap*, the Jaccard similarity of their article sets from earlier, and compared it with how strongly the pair moved together on the same night. The two are related, at a correlation of +0.34, but loosely. The strongest same-night correlations came from pairs that share almost nothing: AI and technology against valuation (r = −0.66, overlap 0.03), defense against crypto (−0.65, overlap 0.09), defense against healthcare (+0.62, overlap 0.05). Meanwhile the most overlapping pair, two finance communities that are frankly the same coverage wearing different hats, correlated at a modest +0.33. A semantic similarity filter would have dutifully removed pairs that weren't causing the problem and left the ones that were.

What overlap *is* good for is catching units that are twins in disguise, before anyone draws a loop between them. `cld_units.py` now scores every unit pair two ways. Article overlap is the Jaccard similarity of the two units' article sets. *Source overlap* compares the two units' mixes of publishers, using a histogram intersection:

```
source_overlap(A, B) = Σ_d min( p_A(d), p_B(d) )
```

where `p_A(d)` is the fraction of unit A's articles that came from source domain `d`. It runs from 0 (no publishers in common) to 1 (identical mixes). Neither measure works everywhere. Article overlap is always zero for topics, since every article belongs to exactly one topic, but source overlap catches topics that share feeds. For communities it's the other way around: a few big publishers feed most of them (a median source overlap of 0.52), and source overlap there didn't track same-night co-movement at all, so it's applied to topics only. A pair counts as a *near-duplicate* when its article overlap reaches 0.4, or, for topics, its source overlap reaches 0.05.

On this archive that flagged three pairs. Artificial Intelligence and Data Science, and Biotechnology and Genomics, partly draw on the same feeds. The two finance communities share 61% of their articles. What happens to a flagged pair is deliberately modest: the LLM is told about it (more on that in a moment), same-night evidence between the two can no longer count as support, and any loop running through the pair is marked as such in the report.

Our heroine did not reach for embeddings, for three reasons. Overlap measures the exact mechanism that inflates a correlation, where an embedding would only approximate it. The tag-level LLM filter from Attempt One already is a semantic filter, and the best-performing part of that whole experiment ("Watermark" and "SynthID" are one product, it noted, correctly). And bringing an embedding model into a project that currently has none is a scope decision worth making on purpose, not by reflex.

## The Robot Proposes, Blindfolded

Here is the part where `cld_units.py` reverses the approach from Attempt One. Instead of letting the data propose links and asking the LLM to filter them, the LLM proposes and the data checks.

The LLM gets a description of every unit: its top tags, the latest strategic bullets for that topic or the closest recent daily community summaries, a few recent headlines, the most recent cross-topic overview, the latest bridge tags, and the list of near-duplicate unit pairs, with an instruction not to build a loop solely between the two halves of a pair. It gets **no statistics whatsoever**. This is the single most important design decision in the whole script. If the LLM could see the correlations, it would draw the correlations, and then "checking the diagram against the data" would amount to checking the data against itself. A hypothesis that has already peeked at the answer key isn't a hypothesis.

The model returns a structured response (a Pydantic model, parsed by instructor, same as everywhere else in the pipeline): one variable per unit it chooses to use, named as a measurable quantity that can rise or fall, and a list of directed links, each with a polarity, a delayed flag, a confidence, and a one-sentence mechanism. The model's validator enforces the rules mechanically: each unit gets at most one variable, every link must point between declared variables, no self-links, and no more than `--max-links` links (22 by default). An answer that breaks a rule is sent back to the LLM with the validation error attached, and it tries again. The prompt also tells the model, explicitly, that balancing feedback is common in real systems and should be included where a genuine counter-pressure exists, because left to its own devices an LLM will draw a world in which everything enthusiastically causes more of everything else.

Each variable also declares a *direction*: `+1` if it rises when its unit gets more coverage, `−1` if it falls. This matters more than it looks. If the crypto community's variable is named "Market Stability," then a big night of crypto coverage probably means stability went *down*, and any statistical check has to flip its expectations accordingly. Every link therefore carries an *expected coverage-level sign*:

```
s = polarity · direction_source · direction_target
```

where each term is `+1` or `−1`. If `s = +1`, the two units' series should rise and fall together; if `s = −1`, they should move in opposite directions. This is the number the data will eventually be asked to confirm.

The mechanics are unglamorous. Each draft is one call through the same `LLMClient` the nightly pipeline uses, with the same model, about ten thousand tokens of context, and structured JSON output, and `cld_units.py` makes three of them in parallel per unit set. The exact prompt is saved next to the diagram, as is each draft, so any henchman who doubts the robot can read precisely what it was shown and what it said back. What it's shown depends on the kind of unit:

| | Topics | Communities |
|---|---|---|
| For each unit | Its top ten tags, up to four of the latest strategic bullets, and up to four headlines from the latest run | Its top ten tags, which defined topics its articles come from, the two closest daily community summaries from the last five runs, and up to four headlines from the latest run |
| For the whole archive | The latest cross-topic overview, the latest bridge tags, and the near-duplicate unit pairs | The same |

It's worth being equally clear about what the robot does *not* do, because it would be easy to assume it's doing more of the work than it is. It never sees a correlation. It doesn't judge whether any link is supported, it doesn't find the loops, and it has no say in what color anything is drawn. All of that is arithmetic, done afterward, by code that can't be talked into anything. Its one-sentence mechanisms and its self-reported confidence are its own reasoning, recorded faithfully and verified by nobody; confidence is used for display and, in a genuine tie between two conflicting versions of one arrow, as the last tie-breaker, and for nothing else.

This is, for the record, the third different job our heroine has given an LLM in this project, and keeping them straight turns out to matter:

| Where | The LLM's job |
|---|---|
| Attempt One (tag-level) | **Judge**: keep or reject each existing Systems Signals pair |
| `cld_units.py` | **Propose**: draft the whole diagram, blind |
| `cld_rating.py` (see the confessions near the end) | **Rate**: say whether each diagram variable rose or fell, from one run's articles |

So far the judge has been the easiest of the three to trust, its rejections grounded and stable from sample to sample, which is mildly humbling for the other two and probably says something about how much easier it is to criticize a diagram than to draw one.

## Three Robots, One Diagram: Agreement Voting

LLMs are not deterministic, and a single sample is a single opinion. So `cld_units.py` asks three times (by default, `--runs 3`) at a fairly lively temperature of 0.7, and keeps a link only if at least two of the three samples agree on it (`--min-agree 2`).

"Agree" needs defining carefully, because samples love renaming things. One sample's "AI Infrastructure Investment" is another's "Data Center Buildout." So variables are identified by their unit, not their name, and links are matched on the triple `(source unit, target unit, s)`, with `s` being the coverage-level sign from above, rather than on polarity alone. That way, two samples that name a variable in opposite directions ("Market Stability" versus "Market Turmoil") but describe the same underlying relationship still count as agreeing. The displayed diagram then uses the most common name and direction for each variable, and recomputes each link's displayed polarity so it stays consistent with them:

```
polarity_display = s · direction_display_source · direction_display_target
```

Matching votes on `s` has one side effect worth confessing. If one sample says AI Agent Deployment Scale *raises* AI-Driven Labor Displacement and another says it *lowers* it, those are two different links as far as the vote is concerned, and if both pass the threshold, the diagram gets two arrows pointing the same way between the same two nodes, cheerfully contradicting each other. At the default two-of-three vote that's arithmetically impossible: both versions would need two votes each, which takes four samples. But loosen the vote to `--min-agree 1`, or ask for more samples, and it happens: seven pairs on the topic side and one on the community side, in the existing drafts. So the diagram now draws exactly one arrow per direction between any two nodes. The version more samples agreed on wins; delay marks break ties, then confidence. Our heroine's first version of this rule put the delay marks first, on the theory that a delayed arrow carries more information. It took about one test run to notice that this let a single sample overrule two others on the arrow's polarity just by marking it delayed, which in turn could flip a whole loop between reinforcing and balancing. Majority rule it is. The price is that when the majority's version has no delay marks and a lone dissenter's does, the arrow is drawn without them. Nothing is thrown away silently, though: every dropped alternative is listed in the report's warnings, since two samples disagreeing about which way an arrow pushes is itself worth knowing.

Our heroine's earlier experiments taught her what this vote is and isn't good for. It is excellent at removing one-off inventions: a loop that appears in only one sample is, almost by definition, something the model made up on the spot. It is not a truth detector. Three samples from the same model can share the same blind spot, and during the tag-level attempt, a link the earlier careful run had rejected sailed through a three-sample vote unanimously at the higher temperature. Agreement measures *stability*. Truth is the next section's job.

## The Data Checks the Robot's Homework

For every surviving link, `cld_units.py` computes the *lagged* Pearson correlation between the source unit's series and the target unit's series, shifted by `lag` runs:

```
r(lag) = corr( x_t , y_(t+lag) )      for lag = 0, 1, 2, 3
```

computed over every run `t` where both values exist, and only when at least eight aligned pairs remain after the shift. (Lag is counted in runs, not calendar days, for the same reasons the previous dispatch already confessed to.)

Each correlation gets a two-tailed p-value through the standard t-transform:

```
t² = r² · df / (1 − r²)        df = n − 2 − controls
p  = I_( df / (df + t²) )( df/2 , 1/2 )
```

where `I` is the regularized incomplete beta function and `controls` is 2 for the bipartial-correlated urgency series (each OLS residualization costs a degree of freedom) and 0 for the CLR shares. For the CLR shares this is exactly what `scipy.stats.pearsonr` would report; for the urgency series it is deliberately stricter, since `pearsonr` knows nothing about the controls and would use `n − 2` degrees of freedom instead of `n − 4`. Either way it's computed from scratch by the same scipy-free helper Systems Signals already uses, because the project still refuses to take on a numerical dependency just to compute one integral.

Then comes the multiple-comparisons correction, because every unit pair at every lag in both directions is a test, and seventeen topics produce 1,088 of them. Benjamini-Hochberg, same as Systems Signals: sort all `m` p-values ascending, and give the `i`-th smallest the q-value

```
q_(i) = min over j ≥ i of ( p_(j) · m / j ),   capped at 1
```

so a q-value of 0.05 means "if I call everything at or below this a discovery, I expect about one in twenty of those discoveries to be noise."

Finally each link gets a verdict, per measure:

- **Supported**: some lag has `q ≤ 0.05`, `|r| ≥ 0.4`, and a correlation sign matching the link's expected sign `s`.
- **Contradicted**: same significance bar, but the correlation points the *wrong* way.
- **Mixed**: significant results in both directions at different lags, which is the data shrugging in two directions at once.
- **Expected**: the only strong evidence is same-night co-movement between a near-duplicate pair. Between twins, moving together on the same night is what you'd predict from shared coverage alone, so it's reported as expected rather than as support. Lagged evidence between them still counts normally.
- **Weak**: nothing survives the correction, but some lag has the right sign, `|r| ≥ 0.3`, and an uncorrected `p ≤ 0.05`. Worth a glance, not worth a claim.
- **Not detected**: everything else.

For topics, which have two measures (shares and urgency), the verdicts are combined: any contradiction alongside any support makes the link mixed; otherwise the strongest verdict wins, in the order supported, contradicted, expected, weak.

## Finding the Loops

With the links settled, loop-finding is pure graph theory, and NetworkX's `simple_cycles` does the heavy lifting: it enumerates every directed cycle that doesn't revisit a node, up to `--max-loop-length` nodes long (six by default). The script keeps the shortest ones first, up to `--max-loops` of them (ten by default), and classifies each by the parity rule from the beginning of this article: count the `-` links around the cycle, and an odd count makes it balancing.

The loops are listed, with their type and each edge's evidence verdict, in the markdown report that accompanies every diagram, and any loop that runs through a near-duplicate pair says so, since a loop between two units that are mostly the same coverage is closer to circular reasoning than to a feedback loop. They are deliberately *not* drawn as the little circular R and B icons the inator normally places inside each loop. Those icons are charming on a hand-drawn diagram with three loops. On a machine-drafted diagram with eight overlapping ones, they turned into confetti.

## From Hypothesis to Picture

The last step writes the inator's text format, one line per link, and hands it over. A few lines of it look roughly like this:

```
title: Causal Loop Hypothesis: communities

AI Infrastructure Investment -> Semiconductor Demand: + #9e9e9e
Energy Commodity Prices -> Construction Activity: - #9e9e9e
Defense Modernization Spending -> Manufacturing Automation Adoption: + #f9a825
```

Each line is `source -> target: polarity`, optionally followed by `delay` and a color. The diagram is rendered in the inator's no-color mode, with each edge's color overridden by its *evidence* rather than its polarity: green for supported, amber for weak, purple for mixed, light blue for expected, red for contradicted, and grey for not detected. The `+` and `-` labels beside each arrow carry the polarity. The effect is a diagram you can read in two layers at once: the shape is the LLM's hypothesis, and the colors are the archive's opinion of it.

For anyone squinting at one of these diagrams, the full key:

| Arrow color | Verdict | What the archive is saying |
|---|---|---|
| Green | Supported | At some lag, the correlation clears the corrected significance bar, is at least moderately strong, and points the way the link predicts. |
| Amber | Weak | Nothing survives the correction, but something leans the right way. Worth a glance, not a claim. |
| Purple | Mixed | Significant evidence pointing both ways at once. |
| Light blue | Expected | The only evidence is same-night co-movement between two near-duplicate units, which their shared coverage would produce anyway. |
| Red | Contradicted | Significant and strong, but pointing the *opposite* way from the arrow's claim. |
| Grey | Not detected | The archive has no opinion yet. |

One warning for henchmen who have used the inator before: on its own, the inator colors arrows by polarity, green for `+` and red for `-`. Here those colors mean evidence instead, so a red arrow can be a perfectly positive link that the data happens to disagree with. When in doubt, read the little `+` or `-` beside the arrowhead, which never lies about polarity, only about whether anyone has checked it.

## What the Archive Said

Here are both unit sets as of 2026-10-13, thirty-one runs of history:

| | Topics (17) | Communities (15) |
|---|---|---|
| Consensus links | 19 | 20 |
| Loops | 4, one balancing | 8, one balancing |
| Supported / contradicted / weak / not detected, before the overlap check | 8 / 7 / 2 / 2 | 0 / 0 / 3 / 17 |
| After the overlap check | 4 supported, 4 expected, 7 / 2 / 2 | unchanged |
| Significant share tests | 142 of 1,088, only 2 lagged | 8 of 840, none lagged |
| Significant urgency tests | 0 of 1,088 | n/a |

The diagrams themselves are genuinely good. They have measurable variables ("Inflation Pressure," "AI Infrastructure Investment," "Semiconductor Demand"), several real feedback loops, and balancing feedback: Clean Energy Manufacturing Scale pushing back against Inflation Pressure on the topic side, and Institutional Investor Activity driving Stock Valuation Pressure, which then dampens Market Sentiment, on the community side. The community version reads as the more coherent economic model, probably because communities are cleaner themes than feed-defined topics. They were, after all, discovered rather than decided. (One caveat the overlap check added: that community-side balancing loop, and a reinforcing loop between Market Sentiment and Institutional Investor Activity, both run through the two near-duplicate finance communities, so they are flagged as such.)

The verdicts, unfortunately, deserve an asterisk the size of a death-ray. **Every single supported or contradicted topic link came from a lag-zero correlation.** "Supported" here means two topics' shares rose and fell together on the same night, and "contradicted" means they moved in opposite directions on the same night. Neither says anything about one driving the other, which is the entire claim an arrow on a causal loop diagram makes. Our heroine checked whether this same-night co-movement was just the publishing calendar in disguise: correlations with Monday runs and with total article volume were all below 0.5, so that's not the whole story. Shared feeds explain part of it: the AI and Data Science topics share two feeds and five source domains, which goes a long way toward explaining their r = 0.87. Re-scoring the same drafts under the overlap check turned exactly those links into "expected": the two-node loops between AI and Data Science and between Biotechnology and Genomics, four of the eight "supported" links. (The new prompt instruction hasn't had its own run yet, so these are the old drafts graded by the new rule.) The other four survive, and the overlap check has nothing to say about them: Inflation Pressure and Consumer Value-Seeking Behavior, for instance, move together at r = +0.90 on the same night with no shared articles or sources at all. That remains unexplained, which in this line of work is not the same as interesting.

The single most satisfying result was one the LLM *didn't* propose. The archive found Defense coverage leading Geopolitics coverage by three runs (r = +0.63, q = 0.005). The LLM had drawn that arrow pointing the other way, Geopolitics driving Defense, which the data rated only weak. It's one data point, but it's the exact behavior a propose-then-check design exists to produce: a hypothesis with a direction, a measurement with a direction, and a disagreement between them worth reading the articles over.

The bottom line is the honest one. Proposing first and checking second works beautifully as a diagram generator. But article share measures how much gets *written* about something, not what's happening to it, and after thirty-one runs it cannot confirm or refute a causal link. The diagram is, for now, a clearly labeled hypothesis. That is still useful next to a strategic report. It's just not a finding, and our heroine has learned the hard way to keep those two words in separate drawers.

## Knobs for the Ambitious Supervillain

For those who want a bigger, louder diagram, every constraint that shapes it is now a command-line flag:

| Flag | Default | What it does |
|---|---|---|
| `--units` | both | `topics`, `communities`, or both |
| `--runs` | 3 | How many independent samples the LLM drafts |
| `--min-agree` | 2 | How many samples must agree on a link; `1` keeps the union of everything, inventions included (where samples disagree on an arrow's polarity, the majority version is drawn) |
| `--temperature` | 0.7 | How adventurous the samples are |
| `--target-communities LO HI` | 15 20 | The community count the resolution sweep aims for |
| `--max-links` | 22 | The most links one sample may propose |
| `--max-loops` | 10 | The most loops listed per diagram |
| `--max-loop-length` | 6 | The longest cycle, in nodes, that gets looked for |
| `--overlap-articles` | 0.4 | Article overlap at which two units count as near-duplicates |
| `--overlap-sources` | 0.05 | Source overlap at which two topics count as near-duplicates |

A word of caution on `--target-communities`: on this archive, asking for 25 to 30 communities lands at resolution 2.0 with a seed stability of only 0.73, while asking for 40 to 60 lands at resolution 4.0 with 48 communities and a stability of 0.93. Bigger isn't monotonically worse, but it isn't monotonically anything, so check the stability line in the log before trusting a partition. The script also saves each run's settings to a `config.json` next to its output, so anything downstream can rebuild exactly the same units and links later.

## What's Deliberately Not Here Yet

In keeping with tradition, the confessions:

- **It's not in the nightly report.** `cld_units.py` lives in `prototypes/causal_loops/`, is not called by the CLI's `run` command, the Prefect flow, or the Airflow port, and persists nothing to the tracking database. It reads, it drafts, it renders, and it leaves. Wiring it into the pipeline would mean deciding how often a diagram should be redrafted, which is a different question from how often the news arrives.
- **No lagged evidence yet.** Not one proposed link in either unit set has lagged support. Same-night co-movement is all the archive currently offers, and it doesn't count.
- **Still no semantic similarity filtering, on purpose.** The overlap check catches twins that share articles or feeds. It can't catch two units that are about the same thing while sharing neither, and it does nothing about the strongest same-night co-movements, which come from pairs with no overlap at all.
- **The partition isn't versioned.** The frozen community partition is rebuilt from the cumulative graph every time the script runs, and it drifts as history grows. A production version would freeze a partition deliberately, keep it, and rebuild it on purpose.
- **Coverage is the wrong thermometer.** The deepest limitation is the measure, not the method: shares count articles, and articles are a measure of attention, not of the world. A sequel prototype, `cld_rating.py` (on the same branch), tried asking an LLM to rate each diagram variable's actual movement every run instead. It came back negative in an instructive way: variables framed as growth trends ("Adoption," "Scale," "Investment") get rated "rising" nearly every single day, and a series that never changes can't correlate with anything. A follow-up experiment, not yet committed, checked whether the rater can track anything real at all by comparing its ratings for oil, Bitcoin, the S&P 500, the VIX, the dollar, and the 10-year yield against actual market data. The ratings did track the market, at Spearman correlations of +0.47 to +0.75, but against the *previous* trading session's move rather than the current one: the articles in a run describe yesterday's news. That's a promising enough result to justify the next round of scheming, which is a story for another dispatch.

## Conclusion

The previous dispatch ended with an archive that could finally say "nothing yet" honestly. This one ends with an archive that can draw a picture of what it *thinks* might be happening, color-code which parts of that picture it can vouch for, and admit that right now the answer is "the shape, mostly, and none of the arrows." Getting there took a frozen Louvain partition with a tuned resolution, a centered log-ratio transform to stop the pie charts from lying, a leave-one-out bipartial correlation borrowed from Systems Signals, an overlap check to stop twins from being diagrammed as cause and effect, a blindfolded LLM, a three-sample vote, a scipy-free incomplete beta function, Benjamini-Hochberg again (it never really leaves), and a separate rendering project to turn the whole thing into something a henchman could understand at a glance. Whether that adds up to the empire finally understanding its own feedback loops, or merely to a very elaborate way of drawing optimistic arrows and then grading them harshly, is a question our heroine has once again decided not to examine too closely. The archive grows by one run every night. Eventually it will have enough history to say something about the arrows too.

## Code

The full source code for this project is available on GitHub at [strategic-report-generator](https://github.com/badass-data-science/strategic-report-generator), in `prototypes/causal_loops/` on the `causal-loop-prototype` branch at the time of this writing.

## AI Use Statement

**Human-in-the-loop:** The author first instructed Claude Code to produce the initial draft of this article based on its own memory of building the prototype described within, across the same session that built it. Then she ruthlessly edited it.

## Tags

- causal loop diagram
- systems thinking
- systems dynamics
- feedback loops
- reinforcing loop
- balancing loop
- Louvain community detection
- modularity
- Jaccard similarity
- histogram intersection
- semantic similarity
- compositional data
- centered log-ratio transform
- bipartial correlation
- lagged correlation
- Pearson correlation
- Benjamini-Hochberg procedure
- false discovery rate
- LLM
- structured outputs
- self-consistency
- time series
- data science
- strategic intelligence
