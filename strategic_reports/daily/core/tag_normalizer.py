"""
Tag normalization for article summary tags.

Normalization runs in two passes:
  1. Programmatic: lowercase, strip, collapse whitespace, replace hyphens with spaces.
  2. Synonym map: map known variants (abbreviations, plurals, alternate forms) to a
     canonical spelled-out singular form.

This runs as a Pydantic field_validator on ArticleSummary.tags, so every tag that
comes out of the LLM is normalized before it reaches the rest of the pipeline.
"""

# Maps lowercased, hyphen-collapsed variants to their canonical form.
# Keys should already be in the programmatically normalized form (lowercase,
# spaces not hyphens) so the lookup works after the first pass.
_SYNONYMS: dict[str, str] = {
    # --- abbreviations → spelled out ---
    "ai": "artificial intelligence",
    "ml": "machine learning",
    "llm": "large language model",
    "llms": "large language model",
    "nlp": "natural language processing",
    "cv": "computer vision",
    "rl": "reinforcement learning",
    "dl": "deep learning",
    "nn": "neural network",
    "nns": "neural network",
    "api": "application programming interface",
    "apis": "application programming interface",
    "ipo": "initial public offering",
    "ipos": "initial public offering",
    "m&a": "mergers and acquisitions",
    "vc": "venture capital",
    "pe": "private equity",
    "esg": "environmental social governance",
    "gdp": "gross domestic product",
    "us": "united states",
    "usa": "united states",
    "uk": "united kingdom",
    "eu": "european union",
    "un": "united nations",
    "who": "world health organization",
    "fda": "food and drug administration",
    "cdc": "centers for disease control",
    "dod": "department of defense",
    "doj": "department of justice",
    "sec": "securities and exchange commission",
    "ehr": "electronic health record",
    "ehrs": "electronic health record",
    "ehr system": "electronic health record",
    "r&d": "research and development",
    "b2b": "business to business",
    "b2c": "business to consumer",
    "saas": "software as a service",
    "paas": "platform as a service",
    "iaas": "infrastructure as a service",
    "roi": "return on investment",
    "kpi": "key performance indicator",
    "kpis": "key performance indicator",
    "ceo": "chief executive officer",
    "cto": "chief technology officer",
    "cfo": "chief financial officer",
    "coo": "chief operating officer",

    # --- plurals → singular ---
    "strategies": "strategy",
    "markets": "market",
    "companies": "company",
    "technologies": "technology",
    "investments": "investment",
    "regulations": "regulation",
    "algorithms": "algorithm",
    "models": "model",
    "networks": "network",
    "systems": "system",
    "applications": "application",
    "opportunities": "opportunity",
    "threats": "threat",
    "trends": "trend",
    "policies": "policy",
    "elections": "election",
    "mergers": "merger",
    "acquisitions": "acquisition",
    "startups": "startup",
    "innovations": "innovation",
    "breakthroughs": "breakthrough",
    "drugs": "drug",
    "treatments": "treatment",
    "vaccines": "vaccine",
    "trials": "trial",
    "patients": "patient",
    "hospitals": "hospital",
    "semiconductors": "semiconductor",
    "chips": "chip",

    # --- synonym consolidation ---
    "generative ai": "generative artificial intelligence",
    "gen ai": "generative artificial intelligence",
    "large language models": "large language model",
    "machine learning model": "machine learning",
    "artificial intelligence model": "artificial intelligence",
    "deep learning model": "deep learning",
    "neural networks": "neural network",
    "supply chain": "supply chain",          # already correct; listed for visibility
    "supply-chain": "supply chain",
    "climate change": "climate change",
    "global warming": "climate change",
    "united states of america": "united states",
    "cyber security": "cybersecurity",
    "cyber-security": "cybersecurity",
    "data science": "data science",
    "data engineering": "data engineering",
    "open source": "open source",
    "open-source": "open source",
    "real time": "real time",
    "real-time": "real time",
    "health care": "healthcare",
    "health-care": "healthcare",
}


def normalize_tag(tag: str) -> str:
    """Normalize a single tag to its canonical form."""
    # Pass 1: programmatic normalization
    tag = tag.lower().strip().replace("-", " ")
    tag = " ".join(tag.split())  # collapse internal whitespace

    # Pass 2: synonym lookup
    return _SYNONYMS.get(tag, tag)


def normalize_tags(tags: list[str]) -> list[str]:
    """Normalize a list of tags and deduplicate, preserving first-occurrence order."""
    seen: set[str] = set()
    result: list[str] = []
    for tag in tags:
        normalized = normalize_tag(tag)
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result
