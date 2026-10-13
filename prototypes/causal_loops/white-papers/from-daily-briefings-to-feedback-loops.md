TITLE:  From Daily Briefings to Feedback Loops

SUBTITLE:  Machine-Assisted Strategic Intelligence: An Accumulating News Archive, Its Knowledge Graphs, and Evidence-Graded Causal Loop Diagrams

**White Paper** · October 2026 · Draft for review

---

# Executive Summary

Strategic decision-makers do not lack information; they lack *structure*. A daily briefing compresses one day's news into a handful of recommendations and is then, in most organizations, forgotten. The accumulated record of what was reported, how topics related to one another, and how those relationships changed over time is discarded at exactly the moment it starts to become valuable.

This paper describes an open-source system, the Strategic Daily Report Generator, that treats each day's briefing as one observation in a growing longitudinal record. Each night it reads news across seventeen strategic domains, uses large language models (LLMs) to summarize and synthesize that coverage into a daily report (with actionable recommendations), and persists every intermediate result to a relational archive with full provenance. From that archive it builds two complementary knowledge graphs, and, in an experimental extension, drafts *causal loop diagrams* (CLDs): maps of the feedback relationships thought to connect the domains it monitors, with each relationship graded against the archive's own time series.

The central design principle throughout is a separation between what a language model is allowed to assert and what the data is allowed to confirm. Language models write summaries, name concepts, and propose hypotheses; graph algorithms and statistical tests decide what counts as structure and what counts as evidence.

**Key findings**

- **Archiving changes what a daily briefing is worth.** Persisting every article, article tag, summary, and score, linked to the run that produced it, converts a disposable report into a queryable record. As of 12 October 2026 the archive held 31 runs and roughly 17,600 summarized articles.
- **Graph structure is a reliable grounding signal for LLM synthesis.** Article tags that appear across three or more domains on the same day ("bridge tags") are computed without any LLM involvement and supplied to the cross-domain synthesis as leads to be confirmed, not conclusions to be repeated.
- **Standards-based knowledge graphs make the archive portable.** An RDF export built on SKOS, PROV-O, and schema.org lets the archive be merged with other institutional knowledge bases without bespoke integration.
- **Machine-drafted causal loop diagrams are feasible and readable.** A propose-then-check method, in which an LLM drafts a diagram without seeing any statistics and the archive then grades each link, produces coherent diagrams with both reinforcing and balancing feedback.
- **Thirty-one runs cannot yet confirm causal structure.** No proposed link has lagged statistical support. Most apparent support is same-day co-movement, which carries no information about direction. Article coverage measures attention rather than real-world change; a preliminary test suggests LLM ratings of real-world movement track market data, but with a one-session lag. As more data accumulates this view will sharpen.

**Recommendations, in brief.** Treat machine-drafted CLDs as labelled hypotheses, not findings; invest first in better measurement of the variables such diagrams contain; version the analytical partitions on which longitudinal comparisons depend; and continue accumulating history, which is the binding constraint on every quantitative claim this system can make.

---

# 1. Introduction: The Problem of Strategic Attention

Strategic intelligence work has always faced a tension between breadth and depth. An analyst responsible for a dozen domains (artificial intelligence, energy, defense, currency markets, biotechnology, and so on) cannot read everything published about each of them, and the cost of missing a cross-domain development is often higher than the cost of missing a development within a single domain. Automated summarization addresses the breadth problem. It does not, by itself, address depth, because depth depends on memory: on knowing whether today's development continues yesterday's, whether two domains that appear together today have appeared together before, and whether movements in one domain tend to precede movements in another.

Conventional automated briefings are stateless. Each day's report is generated from that day's inputs and stands alone. Whatever structure exists across days, such as recurring clusters of concern, slowly strengthening connections between domains, or feedback relationships, is invisible to a system with no memory of its own output.

The system described in this paper was built around the opposite assumption: that the daily briefing is the least durable thing it produces. Its more important outputs are the archive the briefings accumulate into, the graphs that archive supports, and, increasingly, the analytical models that can be built on top of both. Section 2 describes the briefing pipeline. Section 3 describes the archive and its knowledge graphs. Section 4 describes the experimental causal loop diagram method and what it has found. Sections 5 and 6 assess the system's limitations and set out recommendations.

---

# 2. System Overview: The Daily Strategic Report Generator

## 2.1 Scope and operation

The system monitors seventeen active domains through curated RSS feeds: among them artificial intelligence, biotechnology, blockchain, business strategy, data science, defense, economics, energy, foreign exchange, genomics, geopolitics, healthcare, IoT and edge computing, leadership, retail, and robotics. It runs once daily on a schedule managed by the Airflow orchestration framework, and can also be run on demand from a command-line interface. A typical run considers between roughly 550 and 780 recent articles.

## 2.2 Processing pipeline

Each run proceeds in stages:

1. **Ingestion.** Feeds for all domains are fetched concurrently. Network-bound work proceeds in parallel; the synchronous feed parser is isolated in worker threads.
2. **Summarization and per-domain synthesis.** For each domain, articles are summarized in batches by an LLM, each summary consisting of exactly three bullets and between five and twenty normalized article tags. The domain's summaries are then synthesized into three to five strategic recommendations, each accompanied by an urgency score.
3. **Cross-domain synthesis.** A single LLM call reads every domain's recommendations, together with that day's bridge tags (Section 3.3), and writes three to four cross-cutting strategic observations.
4. **Historical comparison.** Each domain's recommendations are compared with the previous run's, and classified as new, continued, or dropped.
5. **Alerting.** A domain's urgency score, and each tag's share of the day's coverage, are compared against their own historical baselines; values more than two standard deviations above the baseline are flagged once at least seven prior runs exist. Until then, urgency scores fall back to a fixed absolute threshold, and tags with too little history are not assessed.
6. **Rendering and archiving.** The day's report is rendered as linked HTML pages, a tag co-occurrence graph is built and rendered as an interactive visualization, every intermediate result is written to the archive, and, on scheduled runs, an RDF export of the full archive is regenerated.

## 2.3 Design principles

Several engineering choices recur throughout the system and bear directly on the reliability of its outputs.

- **Structured outputs, validated at parse time.** Every LLM response is parsed directly into a typed schema. Constraints such as "exactly three bullets" or "at most one variable per domain" are enforced by the schema, and a non-conforming response is returned to the model, with the validation error attached, for correction. No free-text parsing occurs.
- **Provider independence.** All model calls go through a single abstraction (litellm) addressed by a model string, so the system can run against locally hosted or commercial models without code changes.
- **Failure isolation.** A failure in one domain, such as an unreachable feed or a model timeout, produces an error notice for that domain only and never aborts the others. At the storage layer, each row of a bulk write is isolated so that a single malformed record cannot silently discard the rest of a run's data.
- **Explicit concurrency control.** Model calls are throttled with an asynchronous semaphore rather than fixed delays, which keeps throughput within provider rate limits without idle waiting.

---

# 3. From Briefings to Knowledge: The Archive and Its Graphs

## 3.1 The tracking archive

Every run's outputs are persisted to a PostgreSQL database. The archive records, for every run, the articles considered (title, link, publication date, summary bullets, and article tags); each domain's strategic recommendations and urgency score; the day's tag co-occurrence graph (per-tag counts, per-tag domain membership, and tag-pair edges); summaries of the day's tag communities; the bridge tags supplied to the cross-domain synthesis; and the cross-domain observations themselves.

Three properties of the archive matter for analysis:

- **Complete provenance.** Every row is linked to the run that produced it and carries its own timestamp, so any derived result can be traced to its source material and ordered precisely in time.
- **No pruning.** Full history is retained indefinitely. The archive is designed for longitudinal analysis, and analyses of this kind are only as good as the history they can draw on.
- **Explicit schema management.** The schema is defined in versioned migrations, applied deliberately once per environment, rather than created implicitly at connection time.

## 3.2 The nightly article tag co-occurrence graph and its communities

Each night, the system builds a graph in which every article tag is a node and every pair of tags appearing on the same article is connected by an edge weighted by how often that happens. The graph is partitioned with the Louvain community detection algorithm, which groups densely interconnected tags without being told in advance how many groups to find or what to call them [1]. The resulting communities frequently cut across the domain boundaries defined by the feed configuration: for example, a community may draw mostly on biotechnology coverage but include defense and trade articles concerned with the same export-control dispute.

For each community, the system then makes one LLM call, grounded strictly in the articles whose tags belong to that community, to write a paragraph describing what the cluster of coverage is substantively about. A label derived from a community's most frequent tag says what a cluster is called; a grounded summary says what it is about. The nightly graph is also rendered as a self-contained interactive visualization in which color encodes community, node size encodes article count, and edge thickness encodes co-occurrence strength.

## 3.3 Bridge tags: structural grounding for cross-domain synthesis

Cross-domain synthesis is the stage most vulnerable to plausible-sounding invention, because it asks a model to find connections between domains that may not be connected. The system constrains this stage with a purely structural signal. Before the synthesis prompt is written, it identifies *bridge tags*: tags whose articles span three or more domains on the same day. These are supplied to the model as candidate leads, with an explicit instruction to confirm whether each represents a substantive connection rather than repeating it. Every bridge tag supplied is recorded in the archive, so that it is later possible to audit whether the day's cross-domain observations reflected what the graph pointed to.

## 3.4 Graph-guided retrieval over the archive

The archive can be queried in natural language. A question is reduced to candidate tags by an LLM; those tags are matched against every community summary ever written (by exact tag membership first, and by text matching as a fallback); and an answer is synthesized strictly from the summaries retrieved. This is deliberately a modest form of retrieval-augmented generation: it uses the community structure as the retrieval unit and does not use vector embeddings or hierarchical summarization. For the current scope of the tool, the transparency of exact-match retrieval was judged more valuable than the recall of semantic search.

## 3.5 A standards-based knowledge graph

The nightly graph is ephemeral by design: it describes one day and is rebuilt from scratch the next. For integration with other institutional knowledge, the system also exports the entire archive as an RDF knowledge graph in Turtle format, regenerated at the end of every scheduled run. The export reuses established vocabularies wherever one exists:

| Concern | Vocabulary | Mapping |
|---|---|---|
| Concepts and their groupings | SKOS [2] | Tags are `skos:Concept`; communities are `skos:Collection` with `skos:member` tags |
| Provenance | PROV-O [3] | Each run is a `prov:Activity`; every derived fact links to it via `prov:wasGeneratedBy` |
| Bibliographic metadata | schema.org [4] | Articles are `schema:Article` with headline, URL, and publication date |
| Domain-specific facts | `stratrep:` (project namespace) | Domains, urgency scores, bridge-tag observations, and cross-domain overviews |

The project-specific namespace is kept deliberately small, and is extended only when no standard vocabulary offers a suitable term. The same schema.org mapping is embedded as JSON-LD in each published per-domain summary page, so that structured-data consumers without access to the archive receive the same article metadata.

The two graphs are complementary, and the system enforces their separation: the nightly graph serves a human reading one day's news, while the RDF export serves machines integrating the archive's entire history. Neither is computed from the other.

---

# 4. Causal Loop Diagrams: From Structure to Dynamics

## 4.1 Motivation

The knowledge graphs of Section 3 describe *structure*: which concepts appear together, and which domains share concerns. Strategic analysis also requires a view of *dynamics*: which developments drive which others, and whether those influences amplify or dampen one another. The established tool for representing such dynamics is the causal loop diagram (CLD), drawn from the system dynamics tradition [5].

A CLD consists of variables (quantities that can rise or fall), directed links between them marked with a polarity (`+` if the target moves in the same direction as the source, `−` if in the opposite direction), and optional markers for delayed effects. The analytical value of a CLD lies in its closed loops. A loop with an even number of negative links is *reinforcing*, amplifying change; one with an odd number is *balancing*, resisting it. Balancing loops are frequently the less obvious and the more consequential, because they explain why interventions underperform expectations in dynamic systems.

CLDs are conventionally drawn by domain experts. This paper reports an experimental method, implemented as a prototype, for drafting them automatically from the archive and grading each link against the archive's own time series. The prototype is not part of the nightly report.

## 4.2 Method

The method proceeds in six steps.

**Step 1: Define the units.** The nodes of the diagram are *units*, of one of two kinds. The first is the seventeen defined domains. The second is a set of tag communities, which has the advantage of reflecting how coverage actually clusters rather than how feeds were configured. The nightly communities described in Section 3.2 cannot serve this purpose, because they are recomputed independently each day: measured by Jaccard similarity, a community's best match in the previous day's partition shared a median of only 11% of its tags, and no community shared half. The prototype therefore partitions the *cumulative* tag graph, summed across the entire archive, once. It raises the Louvain resolution parameter [6] until the partition contains fifteen to twenty communities with at least five tags each. On the October 2026 archive this produced fifteen communities. Re-running with a different random seed reproduced the partition with a median best-match Jaccard similarity of 0.90.

**Step 2: Construct time series.** For each unit and run, the prototype computes the unit's share of that run's articles. Because each article belongs to exactly one domain, domain shares sum to one, and raw shares therefore exhibit spurious negative correlations: when one domain dominates a day's coverage, every other domain's share falls. Shares are therefore transformed with the centered log-ratio, the standard treatment for compositional data [7]. Community shares do not strictly sum to one, because most articles belong to more than one community, so for communities the transform is an approximation applied to a near-compositional series rather than to a true composition. For the defined domains, a second series is also used: the LLM-scored urgency score, adjusted by bipartial correlation against the mean urgency of every other domain that day, to remove shared "busy news day" effects.

**Step 3: Detect near-duplicate units.** Two units that largely cover the same news will move together for that reason alone, and a loop drawn between them would be closer to circular reasoning than to a feedback relationship. The prototype measures two forms of overlap for every pair: shared articles (Jaccard similarity of the units' article sets) and shared sources (the overlap between the units' distributions of publishing domains). Pairs above fixed thresholds are designated near-duplicates. On the October 2026 archive three pairs met this definition: artificial intelligence and data science, biotechnology and genomics (both through shared feeds), and two finance-related communities that shared 61% of their articles.

**Step 4: Blind proposal.** An LLM drafts the diagram from qualitative context only: each unit's most frequent tags, its most recent strategic recommendations or the closest recent community summaries, recent headlines, the latest cross-domain observations, the latest bridge tags, and the list of near-duplicate pairs, with an instruction not to build loops solely between them. It is shown no statistics. This is the method's most important safeguard. A model that had seen the correlations would reproduce them, and "checking the diagram against the data" would reduce to checking the data against itself. The model names one variable per unit it chooses to use, states whether that variable rises or falls with its unit's coverage, and proposes up to twenty-two directed links, each with a polarity, a delay flag, a confidence level, and a one-sentence mechanism. It is instructed to include balancing feedback where real counter-pressures exist.

**Step 5: Agreement across independent drafts.** Because language model output varies from one sample to the next, three drafts are produced independently, and a link is retained only if at least two agree on its source, target, and expected direction of co-movement. This procedure resembles the self-consistency technique in the language model literature [8]. Where drafts disagree about an arrow's polarity, the majority version is kept and the disagreement is recorded. Agreement filters out one-off inventions; it measures the stability of a hypothesis, not its truth.

**Step 6: Grade each link against the archive.** For every retained link, the prototype computes the lagged correlation between the two units' series at lags of zero to three runs, a two-tailed p-value, and a Benjamini-Hochberg false discovery rate correction across all tests in the scan [9]. Each link receives one of six verdicts, which determine its color in the rendered diagram:

| Verdict | Criterion |
|---|---|
| Supported | Some lag is significant after correction (q ≤ 0.05), at least moderately strong (\|r\| ≥ 0.4), and in the predicted direction |
| Contradicted | The same significance and strength, in the opposite direction |
| Mixed | Significant evidence in both directions |
| Expected | The only strong evidence is same-day co-movement between near-duplicate units |
| Weak | No corrected significance, but a consistent, uncorrected indication in the predicted direction |
| Not detected | None of the above |

Closed loops are then identified by directed-cycle search and classified as reinforcing or balancing. The diagram is rendered with a separate open-source tool. Polarity is shown by a `+` or `−` label beside each arrowhead, and evidence by the arrow's color.

## 4.3 Findings

The method was applied to the archive as of 13 October 2026 (31 runs).

| | Defined domains (17) | Tag communities (15) |
|---|---|---|
| Links retained after agreement | 19 | 20 |
| Closed loops | 4, including 1 balancing | 8, including 1 balancing |
| Supported / contradicted | 4 / 7 | 0 / 0 |
| Expected (near-duplicate units) | 4 | 0 |
| Weak / not detected | 2 / 2 | 3 / 17 |
| Significant coverage-share tests at lags of one run or more | 2 of 1,088 | 0 of 840 |
| Significant urgency tests (defined domains only) | 0 of 1,088 | n/a |

Four findings stand out.

**The diagrams are substantively coherent.** They contain measurable variables ("Inflation Pressure," "AI Infrastructure Investment," "Semiconductor Demand"), multiple feedback loops, and balancing feedback. Examples include clean-energy manufacturing scale dampening inflation pressure among the defined domains, and stock valuation pressure dampening market sentiment among the communities. The community-based diagram reads as the more coherent economic model, consistent with communities being discovered from coverage rather than defined in advance.

**No link has lagged support.** Every supported or contradicted verdict rests on same-day correlation. Same-day co-movement indicates that two domains are reported together; it carries no information about which, if either, drives the other, which is the claim an arrow in a causal loop diagram makes. Neither day-of-week effects nor total article volume explained the co-movement. Shared feeds explained part of it, and the near-duplicate check reclassified the affected links as expected rather than supported. The remainder is unexplained.

**The data can correct the hypothesis.** The archive showed defense coverage leading geopolitics coverage by three runs (r = +0.63, q = 0.005). The drafting model had proposed the opposite direction, which the data rated only weakly. One result is not a pattern, but it is the behavior a propose-then-check method is designed to produce: a directional hypothesis, a directional measurement, and a disagreement that merits human review.

**Coverage is an imperfect proxy for the variables in the diagram.** Article share measures how much is written about a domain, not what is happening within it, and the variables named in a CLD are real-world quantities. This mismatch, rather than any defect in the drafting method, is the principal obstacle to validation.

## 4.4 Toward better measurement

The prototype includes a second experiment addressing the measurement problem directly. Instead of article share, an LLM reads each run's articles for each diagram variable and rates the variable's real-world movement on a five-point scale, or declines to rate it if the articles carry no usable information. The rater is never shown the diagram's links.

Over thirty rated runs, this approach also found no lagged support for any link. The reason was instructive. Variables framed as growth trends ("Adoption," "Scale," "Investment") were rated as rising on nearly every day, producing series with too little variation to correlate with anything. Variables with genuine two-way movement, such as energy commodity prices, crypto-market volatility, and market sentiment, produced usable series.

A preliminary internal check, not yet part of the published code, tested whether such ratings track observable reality at all. It compared the ratings of seven market variables with hourly market data. Ratings for crude oil, Bitcoin, the S&P 500, the VIX, the U.S. dollar index, and the ten-year Treasury yield correlated with actual market movements at rank correlations of +0.47 to +0.75, significant in each case. The correlation was with the *previous* trading session's movement, not the current one: the articles in a given run largely report the prior session. By contrast, article coverage of the same variables showed no relationship to the size of the market moves. These results suggest that LLM ratings can measure fast-moving, frequently reported variables, and they identify a consistent publication lag that any such measurement must account for.

---

# 5. Assessment: What the System Can and Cannot Claim

The system's outputs fall into three tiers of epistemic standing, and its design attempts to keep them distinct.

| Tier | Examples | Basis |
|---|---|---|
| Recorded fact | Articles, tags, co-occurrences, bridge tags, community membership | Direct observation and deterministic computation |
| Grounded synthesis | Article summaries, domain recommendations, community summaries, cross-domain observations | LLM output constrained by schemas and by source material or structural signals |
| Hypothesis | Causal loop diagrams and their mechanisms | LLM proposal, graded but not confirmed by statistical tests |

The principal limitations are as follows.

- **History is short.** With 31 runs, a lagged correlation must exceed roughly r = 0.6 to survive correction, and mechanisms that operate over months cannot be tested at daily resolution regardless of history.
- **Coverage is not reality.** The archive records what was published, which reflects editorial attention and feed composition as well as real-world events.
- **Model judgments are unverified.** The mechanisms, confidence levels, and summaries produced by language models are recorded faithfully but are not independently verified; agreement between samples of the same model can reflect shared bias.
- **Analytical partitions drift.** The frozen community partition is rebuilt from the cumulative archive each time the analysis runs. In an earlier check at the default resolution, a partition built from two-thirds of the archive's history matched the full-history partition at only about 0.71, so longitudinal comparisons require partitions to be versioned deliberately.
- **Some co-movement remains unexplained.** The strongest same-day co-movements between defined domains occur between pairs with no detectable overlap in articles or sources.

None of these limitations is unusual for observational analysis of news data. What distinguishes the system is that each is surfaced in its own outputs: every diagram is accompanied by a report stating, for every link, the evidence behind its verdict.

---

# 6. Recommendations

**For practitioners using machine-drafted causal loop diagrams:**

1. **Present diagrams as labelled hypotheses.** A machine-drafted CLD is a structured statement of what a model believes, with an honest account of how much of it the data supports. It should accompany strategic analysis as a prompt for discussion, not stand in for evidence.
2. **Read evidence and polarity separately.** In the prototype's rendering, arrow color encodes the strength of evidence and the `+` or `−` label encodes polarity. Conflating the two is the most likely misreading.
3. **Discount same-day evidence.** Support at a lag of zero establishes co-occurrence, not influence. Directional claims require lagged evidence.

**For further development of the system:**

4. **Prioritize measurement over modeling.** The binding constraint is the quality of the variable series, not the drafting method. LLM ratings of fast-moving, frequently reported variables, aligned to account for publication lag and validated against external data where it exists, are the most promising path.
5. **Ask for rateable variables.** Diagram drafting should prefer variables that visibly move from one day to the next, such as prices, sentiment, threat levels, and policy actions. Slow structural trends should be retained as context and labelled as untestable at daily resolution.
6. **Version analytical partitions.** Community partitions used for longitudinal analysis should be frozen, versioned, and rebuilt deliberately, so that a node means the same thing across the period being analyzed.
7. **Accumulate history.** Every quantitative claim the system can make improves with history. Continuity of the daily run is the most valuable investment available.
8. **Integrate through standards.** The RDF export allows the archive to be combined with other institutional knowledge, such as operational records, decision logs, or other monitoring systems, without bespoke integration. Combining sources is likely to supply variables that news coverage alone cannot.

---

# 7. Conclusion

A daily briefing is valuable for a day. An archive of briefings, with full provenance and the graph structure to navigate it, is valuable for as long as it is kept, and becomes more valuable with each day it is kept. The Strategic Daily Report Generator demonstrates that such an archive can be built and maintained by a single analyst with open-source tools, that its structure can be exported in standard form for integration with other knowledge, and that it can support an experimental step from structure toward dynamics: machine-drafted causal loop diagrams graded against the record they were drawn from.

The current results are best described as a successful demonstration of method combined with an honest negative result on evidence. The diagrams are coherent; the archive is not yet able to confirm them. That outcome reflects the system's governing principle, that language models may propose but only data may confirm, and it identifies the next priority clearly: better measurement of the quantities the diagrams describe, accumulated over a longer history.

---

# Appendix A: Glossary

- **Bridge tag.** An article tag whose articles span three or more domains on the same day.
- **Causal loop diagram (CLD).** A diagram of variables connected by signed, directed links, used to represent feedback structure.
- **Centered log-ratio (CLR).** A transformation of compositional data (shares summing to one) that largely removes the spurious negative correlation such data otherwise exhibits; because CLR values themselves sum to zero, a small residual bias of about −1/(D−1) between D units remains.
- **Community.** A densely interconnected group of tags identified by the Louvain algorithm.
- **False discovery rate (FDR).** The expected proportion of reported discoveries that are false; controlled here with the Benjamini-Hochberg procedure.
- **Lag.** The offset, in daily runs, between the source variable's observation and the target variable's observation when testing a causal link.
- **Near-duplicate units.** Two units sharing enough articles or sources that their co-movement is expected from overlap alone.
- **Unit.** A node in a causal loop diagram: either a defined domain or a community from a frozen partition.

# Appendix B: Summary of Statistical Methods

- **Jaccard similarity** between sets A and B: `|A ∩ B| / |A ∪ B|`.
- **Modularity** maximized by Louvain, with resolution γ: `Q = (1/(2m)) Σ_ij [A_ij − γ k_i k_j / (2m)] δ(c_i, c_j)`, where `A_ij` is edge weight, `k_i` a node's total edge weight, `m` the total edge weight, and `δ` is 1 when two nodes share a community.
- **Centered log-ratio** of unit k's share in a run: `log((count_k + 0.5)/n) − (1/D) Σ_j log((count_j + 0.5)/n)`, for D units and n articles, with a pseudo-count of 0.5.
- **Source overlap** between units A and B: `Σ_d min(p_A(d), p_B(d))`, where `p_A(d)` is the fraction of A's articles from source domain d.
- **Expected co-movement sign** of a link: `s = polarity × direction_source × direction_target`, each term ±1.
- **Lagged correlation**: Pearson r between `x_t` and `y_(t+lag)`, for lags 0 to 3, requiring at least eight aligned observations; bipartial correlation for urgency series after ordinary least squares adjustment for each side's leave-one-out mean.
- **Significance**: two-tailed p-value from `t² = r²·df/(1 − r²)` with `df = n − 2 − controls`, evaluated through the regularized incomplete beta function.
- **Benjamini-Hochberg q-value** for the i-th smallest of m p-values: `min over j ≥ i of (p_(j) · m / j)`, capped at 1.

# Appendix C: Data and Reproducibility

The system is open source under the MIT license and available at [github.com/badass-data-science/strategic-report-generator](https://github.com/badass-data-science/strategic-report-generator). The causal loop diagram prototype described in Section 4 resides in `prototypes/causal_loops/` on the `causal-loop-prototype` branch at the time of writing, together with a README documenting its command-line options and results. Results reported here were computed against the project's archive as of 13 October 2026 (31 runs, beginning 1 September 2026) using the `glm-5.2:cloud` model. The prototype supports re-running the analysis against the archive as it stood at any earlier point. The market-data validation described in Section 4.4 was a preliminary internal analysis and is not included in the published code.

# References

1. Blondel, V. D., Guillaume, J.-L., Lambiotte, R., and Lefebvre, E. (2008). Fast unfolding of communities in large networks. *Journal of Statistical Mechanics: Theory and Experiment*, 2008(10), P10008. https://doi.org/10.1088/1742-5468/2008/10/P10008
2. Miles, A., and Bechhofer, S. (Eds.) (2009). *SKOS Simple Knowledge Organization System Reference*. W3C Recommendation, 18 August 2009. https://www.w3.org/TR/2009/REC-skos-reference-20090818/
3. Lebo, T., Sahoo, S., and McGuinness, D. (Eds.) (2013). *PROV-O: The PROV Ontology*. W3C Recommendation, 30 April 2013. https://www.w3.org/TR/2013/REC-prov-o-20130430/
4. Guha, R. V., Brickley, D., and Macbeth, S. (2016). Schema.org: Evolution of structured data on the web. *Communications of the ACM*, 59(2), 44–51. https://doi.org/10.1145/2844544
5. Sterman, J. D. (2000). *Business Dynamics: Systems Thinking and Modeling for a Complex World*. Boston: Irwin/McGraw-Hill. ISBN 978-0-07-231135-8.
6. Reichardt, J., and Bornholdt, S. (2006). Statistical mechanics of community detection. *Physical Review E*, 74(1), 016110. https://doi.org/10.1103/PhysRevE.74.016110
7. Aitchison, J. (1986). *The Statistical Analysis of Compositional Data*. London: Chapman and Hall. https://doi.org/10.1007/978-94-009-4109-0
8. Wang, X., Wei, J., Schuurmans, D., Le, Q., Chi, E., Narang, S., Chowdhery, A., and Zhou, D. (2023). Self-consistency improves chain of thought reasoning in language models. *International Conference on Learning Representations (ICLR 2023)*. https://arxiv.org/abs/2203.11171
9. Benjamini, Y., and Hochberg, Y. (1995). Controlling the false discovery rate: a practical and powerful approach to multiple testing. *Journal of the Royal Statistical Society, Series B (Methodological)*, 57(1), 289–300. https://doi.org/10.1111/j.2517-6161.1995.tb02031.x

# Tags

- strategic intelligence
- strategic foresight
- horizon scanning
- open-source intelligence
- policy analysis
- think tank
- knowledge graph
- RDF
- linked data
- SKOS
- PROV-O
- schema.org
- data provenance
- Louvain community detection
- causal loop diagram
- system dynamics
- systems thinking
- feedback loops
- large language models
- LLM
- AI-assisted analysis
- human-in-the-loop
- time series
- false discovery rate
- data science
