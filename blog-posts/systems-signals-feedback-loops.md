# Everything Correlates With Everything (Our Heroine Teaches Her Archive to Doubt Its Own Coincidences)

### A field guide to finding real feedback loops in six months of nightly news, and to not finding fake ones

If you read our heroine's earlier dispatch, ["Strategic Intelligence, Interconnected"](https://badassdatascience.substack.com/p/strategic-intelligence-interconnected), you already know that her nightly strategic report pipeline had graduated from "one disposable HTML file per night" to "an archive with a shape to it" — a tag co-occurrence graph, Louvain communities, a durable RDF export.

That article was about *structure*: what things are connected to what other things, on any given night. It was not about *time*. A knowledge graph, however elegant, is a snapshot. It knows that "export controls" and "biotech" showed up together tonight. It has no opinion at all about whether Tuesday's Energy coverage tends to show up again in Thursday's Forex coverage, or whether a spike in "interceptor" articles this week reliably precedes a spike in "North Korea" articles next week. Structure tells you what the empire looks like. It does not tell you how the empire *moves*.

This is the story of what happened when our heroine decided she wanted the second thing too — and of the considerably larger number of ways that turned out to be wrong before it was right.

## Systems Thinking, Briefly, for People Who Have Actual Jobs

"Systems thinking" is one of those phrases that sounds like a Silicon Valley off-site until you actually need it, at which point it becomes disappointingly useful. The core idea, borrowed from systems dynamics as practiced by people who model national economies and ecosystems rather than RSS feeds, is that the *variables* in a system rarely matter as much as the *loops* between them. A feedback loop is just this: does A's state at one point in time help predict B's state a little later, and — the interesting part — does B then feed back into A, closing the circuit? Reinforcing loops amplify (more of A causes more of B causes more of A, and the whole thing spirals); balancing loops self-correct (more of A causes more of B, which then suppresses A). A causal loop diagram is just a picture of which variables lead which other variables, and by how much delay.

None of that is exotic once you say it out loud. What's exotic is doing it *empirically*, across a genuinely noisy real-world signal, without a systems scientist sitting there drawing arrows from intuition. Our heroine's tracking database already accumulates exactly the kind of time series systems dynamics wants: a per-topic urgency score every run, a per-tag coverage rate every run, for as many runs as the pipeline has ever executed. The question was whether any of that history contained real, detectable lead-lag relationships — candidate feedback loops — or whether it was just the daily news being the daily news.

## What "Systems Signals" Actually Is

Systems Signals is the name our heroine gave the resulting module — `systems_signals.py`, if you're the type who prefers filenames to prose — and its job description is almost insultingly simple to state: for every pair of things the pipeline already tracks (topics, by their LLM-scored urgency; tags, by how often they appear relative to that run's article count), compute the correlation between one thing's value now and the other thing's value some number of runs later. Do that for lag zero (do they move together *right now*) and for a handful of lags greater than zero (does one of them lead the other). Report back anything that looks statistically real.

Two things about that description matter enormously and are worth getting straight before anything else. First, "lag" here means *runs*, not calendar days. The pipeline sometimes executes several times in one day — a manual rerun during testing, say — so "lag one" can mean "the very next run, ninety seconds later" just as easily as it can mean "tomorrow." Second, and more importantly: lag zero and lag-greater-than-zero are not the same kind of claim. Two things moving together in the *same* run is contemporaneous association — interesting, but it says nothing about which one, if either, is driving the other. A real feedback-loop candidate — the thing a systems thinker would actually draw an arrow for — only shows up once you can say A's value predicted B's value in a *later* run, because prediction, not mere correlation, is what a leading indicator requires.

## Naively, Everything Correlates With Everything

Our heroine's first working version of Systems Signals did exactly what the description above says, computed a raw Pearson correlation for every pair at every lag in both directions, and kept anything above a chosen correlation threshold. Against the live database — fourteen accumulated runs, seventeen tracked topics, on the order of seven thousand distinct tags — this produced roughly forty "significant" topic pairs and just under ten thousand "significant" tag pairs.

This should have felt like an embarrassment of riches. It was actually just an embarrassment. Running an all-pairs, all-lags, both-directions scan over seventeen topics and thousands of tags means testing many thousands of hypotheses in a single pass, and if you test ten thousand things at a five-percent significance threshold with no correction, you should *expect* roughly five hundred of them to look significant by pure chance, whether or not anything real is happening. A single p-value answers "how surprising would this correlation be if there were truly no relationship?" It does not answer, and was never designed to answer, "how surprising is it that at least one of my ten thousand tests came back looking this good?" Multiple-comparisons correction exists precisely to close that gap, and Systems Signals needed it badly.

The correction our heroine reached for is the Benjamini-Hochberg procedure, which controls the *false discovery rate* rather than the probability of any single false positive. In plain terms: sort every p-value from a single scan smallest to largest, compare each one against a sliding threshold that gets stricter the further down the sorted list you go, and report as significant only the pairs that clear that sliding bar. The output of that procedure is a *q-value* per candidate — not "how surprising is this one result," but "if I declare everything at or below this q-value significant, what fraction of my declared discoveries do I expect to actually be false positives?" A q-value of 0.05 means our heroine is knowingly accepting that roughly one in twenty of her reported feedback loops could be noise dressed up as a finding — a bet she was comfortable making, once it was an informed bet and not an accidental one.

Running Benjamini-Hochberg across every test performed in a single scan collapsed the raw topic-level output to exactly one surviving pair — Energy and Forex, moving together at lag zero — and cut the tag-level output roughly in half, from around 9,700 pairs to around 4,868. Considerably more honest. Still nowhere near clean enough to trust, which is where the actual work began.

## The Sparsity Floor: When Rare Things Trivially "Correlate"

The first remaining problem had nothing to do with statistics in the abstract and everything to do with what happens when a tag is simply *rare*. Two tags that each appear in only one or two runs out of fourteen will, if those runs happen to overlap even slightly, produce a Pearson correlation of very close to 1.0 — not because the tags mean anything to each other, but because a correlation coefficient computed over almost no data is an extremely blunt instrument, and "both of us were nonzero on the same Tuesday" is not a relationship, it's a coincidence with excellent penmanship. Worse, this isn't a false positive in the statistical sense the Benjamini-Hochberg correction was built to catch — the p-value genuinely is small, because the correlation genuinely is that extreme within the (tiny) sample. No amount of multiple-comparisons correction fixes a problem that lives in the sample size of an individual pair, not in how many pairs got tested.

The fix is a floor, not a test: a tag has to be nonzero in at least five of the tracked runs before it's even eligible to enter the correlation scan at all. This alone cut the tag-level survivor count from around 4,868 pairs down to roughly 244 — a sparsity artifact, quietly and permanently removed, before it ever got the chance to dress itself up as a discovery.

## Near-Synonymous Pairs and the Containment Ratio

The next 244 survivors contained a different flavor of nonsense. Pairs like "qualcomm" and "wireless technology," or "ugg" and "deckers outdoor," correlated almost perfectly — and, unlike the sparsity cases, they had plenty of history behind them. The problem was that these aren't really two variables. They're one entity described by two labels, and any article that mentions one is overwhelmingly likely to mention the other, because Deckers Outdoor *is* the company that makes UGG. A correlation between a thing and its own alias isn't a feedback loop. It's a tautology with extra steps.

Catching this required a different kind of evidence than a bare correlation coefficient could ever supply: a *containment ratio*, computed directly from the tag co-occurrence graph the pipeline already builds every run. For a candidate pair, take the total number of articles where both tags appeared together across the entire archive, and divide by the total number of articles the *rarer* of the two tags ever appeared in, alone or otherwise. A ratio near 1.0 means the rarer tag essentially never shows up without its partner — the two are, for practical purposes, inseparable, and reporting a correlation between them is reporting that a thing correlates with itself.

Any pair whose containment ratio cleared 0.8 got dropped before it ever reached the correlation math at all, on the theory that this is a *structural* judgment about the tag graph, not a statistical one about the time series, and it belongs upstream of the statistics rather than tangled up inside them. This filter alone took the survivor count from roughly 244 down to about 125.

## Topical Adjacency and the Community Filter

A hundred and twenty-five pairs still contained a subtler problem, one the containment ratio was never built to catch: pairs that aren't the *same* entity, but are close enough on the tag graph that a correlation between them isn't remotely surprising. "Electric utility" and "power generation." "Defense" and "military." These aren't aliases — the containment ratio between them sits comfortably below 0.8 — but they're clearly drawn from the same well, and finding that they move together is about as newsworthy as finding that rain correlates with clouds.

Rather than invent a new signal from scratch, our heroine reached for one she'd already built for an entirely different purpose: Louvain community detection, the same modularity-maximizing clustering that partitions the nightly tag co-occurrence graph into structurally-dense neighborhoods for the D3 visualization described in the earlier dispatch.

Two tags that land in the *same* Louvain community in the overwhelming majority of runs where both were assigned a community at all — again, a ratio above 0.8, with at least three runs of shared evidence required before the ratio is trusted at all — get dropped as topically adjacent, on the same "this isn't surprising, so don't report it as a discovery" logic as the containment filter. This is precisely the "start from a graph-native signal instead of inventing something new" instinct that shaped the whole project's approach to bridge tags and community summaries, applied again here. It cut the survivor count from about 125 pairs to about 52.

## Confounds, Partial Correlation, and the Topic-Volume Control

Fifty-two pairs is a small enough number that our heroine did something she probably should have done from the start: she read the actual articles. One of the strongest remaining candidates was "interceptor" leading "North Korea" coverage by one run, with a correlation and p-value that looked, on paper, entirely respectable. Pulling the real articles behind it told a much less exciting story: every single article on both sides of that pair was tagged `[Defense]`, and there was no thread of actual narrative connecting missile-interceptor funding news to North Korea coverage specifically. What had actually happened was that the Defense topic simply had a busy week, and *every* Defense-adjacent tag rose together as a side effect — a shared confound, not a relationship between the two tags themselves.

This is a fundamentally different problem from the previous two filters, because "interceptor" and "North Korea" pass both of them cleanly: they aren't aliases, and they don't reliably land in the same Louvain community. What's needed here isn't an exclusion rule but a *statistical control* — the same logic behind a partial correlation in any introductory econometrics course, just computed from scratch, since the project deliberately carries no scipy or numpy dependency.

For each tag, the pipeline already knows every topic that tag has ever appeared under, and how often — call that the tag's topic profile. Before correlating two tags, each one's rate series gets residualized against a weighted blend of its own topic profile's article volume that run: fit a simple ordinary-least-squares line predicting the tag's rate from "how busy were the topics this tag lives in, this run," and correlate what's left over — the residual — instead of the raw series. Two tags riding the same busy-topic wave together will have that shared wave subtracted out of both of them before they're ever compared. Removing two nuisance variables this way costs two degrees of freedom in the resulting significance test, which the p-value calculation accounts for explicitly.

The first version of this control used only each tag's single *most common* topic. It worked well enough to be genuinely dangerous: it dropped the survivor count from 52 pairs to exactly 2, and both of the remaining two — "drug discovery" correlated with "innovation," and "intraday trading" correlated with "market trend" — had *different* single primary topics on each side, which looked, superficially, like proof the confound had been ruled out.

It hadn't. "Innovation" turned out to be an extremely broad, high-frequency tag spanning business, leadership, food science, and AI content, essentially riding a general "AI industry momentum" wave that spans *several* topics at once rather than living in any one of them — a confound no single-topic control could ever see, because the confound itself doesn't respect a single-topic boundary. The fix was to stop picking one topic per tag and instead use the *entire* weighted profile — every topic a tag has ever appeared under, weighted by how often — as the control variable, which is a strict generalization: a tag whose coverage genuinely is concentrated in one topic gets exactly the old behavior back, for free.

## A Confession About Missing Data

While chasing those two survivors down, our heroine found something considerably more embarrassing than a statistical subtlety: a real bug. Two of the fourteen tracked runs had a healthy, populated tag-count history — proof they'd genuinely happened, with hundreds of real articles apiece — but *zero* rows in the table that stores individual article records.

The cause turned out to be an old architectural flaw in the archiving code: every article in a run was inserted inside one shared database transaction, and if a single article's insert failed for any reason, the entire transaction rolled back, silently discarding every other article from that run along with it — while the separate call that builds the tag graph, drawing from the exact same in-memory data, sailed through untouched. The tag-level tables looked completely normal. The article-level tables were a crime scene.

This mattered enormously for Systems Signals specifically, because the topic-volume control described above reads article counts per topic per run, and the code was defaulting a run with no article rows to a topic volume of *zero* — which is indistinguishable, mathematically, from a topic that genuinely had no coverage that day. A false zero fed into a linear regression doesn't just shrink your sample size the way honestly missing data does; it actively biases the fit, because the regression is told, falsely, that a real busy day was actually dead quiet.

The fix on the archiving side was to insert each article inside its own database savepoint, so one bad row can only lose itself, not the whole run — and the same fix was applied, for the same reason, to the code that persists tag counts and community summaries, which shared the identical fragile all-or-nothing pattern and simply hadn't been caught yet. The fix on the Systems Signals side was to stop treating "no article rows for this run" as a confident zero and start treating it as a gap — the same "skip rather than guess" instinct the rest of the codebase already leaned on for thin history, now applied to a run's data quality rather than just its quantity.

Once both fixes landed, both remaining survivors evaporated. "Drug discovery" and "innovation" fell from a comfortably significant q-value to a thoroughly unremarkable one purely because two of the runs backing that correlation had been silently lying about their own topic volume. "Intraday trading" and "market trend" turned out to be substantially driven by exactly one of the two broken runs — a phantom data point with real-looking numbers and nothing behind them.

## The Single-Source Confound

Cleaning up the missing-data bug reset the survivor count to zero, which was the honest answer but not a satisfying place to stop, because two confounds remained genuinely un-addressed by anything built so far. The first: two tags can correlate strongly not because they move together, but because they're both mentioned in the same *recurring single source*. "Dax" and "Nikkei" — two stock indices — turned out to co-occur mostly because a single daily market-digest article from one recurring publisher routinely discusses both indices in the same piece. That's a fact about one blog's editorial habits, not a fact about German and Japanese equity markets.

The fix reused data the pipeline already had lying around: for every candidate pair, pull every article where both tags actually co-occur, extract each article's source domain from its URL, and compute what fraction of those co-occurrences trace back to a single dominant domain. A ratio above 0.8 — with, again, a minimum of three co-occurring articles required before the ratio is trusted — gets the pair dropped as a single-source artifact before the correlation math ever runs, structurally excluded on the same footing as the containment and community filters. Dax and Nikkei, along with several other pairs like "clearing house" and "exchange," disappeared from the candidate pool entirely once this landed — not by failing a significance test, but by never being considered candidates in the first place.

## What's Left, and How It Gets Read

At this point Systems Signals runs five layered checks before a single correlation is even reported: a sparsity floor, a containment-ratio filter for near-synonymous pairs, a Louvain-community filter for topically adjacent pairs, a weighted topic-volume partial-correlation control for cross-topic confounds, and a single-source-domain filter — on top of the Benjamini-Hochberg false-discovery correction that wraps around whatever candidates survive all five. Against the current fourteen runs of history, exactly one candidate clears every bar: Energy and Forex, moving together at lag zero, with a correlation around 0.92 and a q-value comfortably below the 0.05 threshold.

The tag-level side currently reports nothing at all, and a deliberate sweep of the significance thresholds — loosening them well past their defaults just to see what showed up — confirmed that isn't a bug in the filters. It's a sample-size problem: at fourteen runs, nothing left in the candidate pool is anywhere near strong enough to survive honest correction, and the pairs sitting just below the bar are, without exception, more of the same near-synonymous, topically-adjacent flavor the filters were already built to catch, just below their cutoffs rather than above them.

None of this means the reading process is automatic, and our heroine has been consistently unwilling to trust a q-value on its own. Every candidate that has ever survived the full filter chain — including the ones that eventually turned out to be artifacts — got a manual pass afterward: pull the actual articles behind the runs that drove the correlation, read what they're actually about, and ask whether there's a real thread connecting the two subjects or just a shared statistical wave. That step is what caught the interceptor/North Korea confound in the first place, what caught the phantom run behind intraday-trading/market-trend, and what caught the recurring-digest pattern behind dax/nikkei — none of which a p-value, however small, was ever going to catch on its own.

A lag greater than zero is treated as the more interesting category by default, since only a lagged relationship can support a genuine leading-indicator claim; a lag of zero, even a statistically bulletproof one, is read as "these move together," not "one predicts the other," which is exactly how Energy and Forex are currently presented.

## Putting It Where the Report Can See It

Once the filter chain earned enough trust, Systems Signals stopped being a script run by hand against the database and became a proper section of the nightly report itself — "Topic Urgency" and "Tag Coverage" results grouped separately, each candidate shown with its correlation, its lag, its q-value, and a one-line plain-language gloss of what the lag means. On a day with no results — which, honestly, is most days right now — the section doesn't vanish; it says so plainly, the same way an empty topic already says "no articles found" rather than disappearing and leaving the reader to wonder if something broke.

It's wired into all three of the pipeline's entry points now: the manual CLI command, the Prefect-scheduled flow, and — after a slightly sheepish follow-up once it was noticed the two had drifted apart — the hand-maintained Airflow port of that same flow living in a separate corner of the empire.

## What's Deliberately Not Here Yet

In keeping with previous dispatches' habit of confessing what the Ultimate Cunning Master Plan™ doesn't actually do yet, some honest gaps:

- **No semantic similarity filtering.** Several pairs — "fighter aircraft" and "weapon system," "aircraft carrier" and "carrier strike group," "e commerce" and "retail" — survive every one of the five filters above despite reading as near-definitional pairs to any human. None of the current filters have any notion of *meaning*; they're all graph-structural or statistical. Catching these would mean bringing in embeddings or an LLM judgment call, a materially heavier tool than anything used so far, and a deliberate scope decision to make carefully rather than reach for reflexively.
- **No higher-order loop detection.** Everything here is strictly pairwise — does A predict B — which in systems-dynamics terms is a first-order loop at best. A genuine causal loop diagram often involves three or more variables in a cycle: A drives B, B drives C, C drives back to A. Finding those would mean treating the pairwise lagged relationships as directed edges in a graph and searching for cycles across three or more nodes, a fundamentally different and much harder search than testing every two-subject combination directly, and one that would face an even steeper multiple-comparisons problem than the pairwise version already does.
- **No database tracking of found loops over time.** Every result Systems Signals produces today is computed fresh, on demand, from the underlying time series, and nothing about a given day's findings is ever persisted anywhere — not even the rendered HTML report, which gets wiped and rebuilt from scratch on every single run. There is, at the moment, no way to ask "which candidate loops has the archive found over the last month" or "did this correlation get stronger or weaker over time," because nothing is being kept around to ask that question of. This is a deliberate choice for now, not an oversight — with only fourteen runs of history and a filter chain still earning its keep, our heroine would rather trust the *current* answer thoroughly before she starts building a permanent record of past ones.
- **No calendar-time awareness.** Every "lag" in this system is a lag in *run index*, not in days — a consequence of the pipeline sometimes executing multiple times on the same calendar day during manual testing. As the run cadence stabilizes toward a strict once-daily schedule this distinction will matter less, but it isn't accounted for explicitly anywhere yet.

All of these are candidates for a future dispatch once the underlying archive has enough history to make chasing them worthwhile, rather than half a page of theory wrapped around fourteen data points.

## Conclusion

A knowledge graph told our heroine what her empire's nightly news *looked like*. Systems Signals is the first real attempt at telling her how it *moves* — which pairs of topics or tags actually seem to lead each other, once every obvious way of being fooled has been ruled out one at a time. Getting there took a Benjamini-Hochberg correction, a sparsity floor, a containment ratio, a reused Louvain clustering, a from-scratch partial correlation with no scipy in sight, an embarrassing and eventually-fixed data-loss bug, a source-domain filter, and a standing rule that no candidate gets trusted until someone has actually read the articles behind it.

At the end of all of that, the honest current answer is one real signal and a great deal of hard-won confidence in reporting "nothing yet" instead of something false. That is either an unusually rigorous way to spend several days chasing coincidences, or exactly what systems thinking looks like when you make it show its work. Our heroine has decided, as usual, not to examine that distinction too closely, and to keep watching the archive for the day it finally has enough history to say something more.

## Code

The full source code for this project is available on GitHub at [strategic-report-generator](https://github.com/badass-data-science/strategic-report-generator), on the `systems-thinking-prototype` branch at the time of this writing.

## AI Use Statement

**Human-in-the-loop:** The author first instructed Claude Code to produce the initial draft of this article based on its own memory of building the feature described within, across the same session that built it. Then she ruthlessly edited it.

## Tags

- systems thinking
- systems dynamics
- feedback loops
- causal loop diagram
- Benjamini-Hochberg procedure
- false discovery rate
- multiple comparisons
- p-value
- q-value
- partial correlation
- Pearson correlation
- confounding variable
- Louvain community detection
- time series
- lagged correlation
- statistics
- data science
- strategic intelligence
- Prefect
- Apache Airflow
- data engineering
