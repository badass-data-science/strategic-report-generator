"""
Tag co-occurrence network graph generator.

Three outputs (plus an inline graph per featured report -- see
build_subset_display_graph() and renderer.render_report()):
  tag_graph.json         — full graph (all tags/edges); kept for downstream data science
  tag_graph_display.json — pruned + Louvain-community-annotated graph for the HTML viewer
  tag_graph.html         — D3.js force-directed graph; loads tag_graph_display.json

The full graph is typically 1000+ nodes and 10000+ edges — far too large for a
browser force simulation. The display graph is pruned to nodes with count >=
min_count and edges with weight >= min_weight (defaults: 3 / 2), giving a
manageable ~100-300 node graph. Louvain community detection then assigns each
node a cluster ID, which the HTML viewer uses for color coding.

Graph definitions:
  Nodes  = normalized tags; sized by article count; colored by community
  Edges  = pairs of tags that co-appear on the same article; weighted by count
"""

import json
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any

import networkx as nx

from .models import ArticleSummary, TopicResult


def build_graph_data(results: list[TopicResult]) -> dict[str, Any]:
    """
    Build the full node/edge graph from tag co-occurrence across all articles.

    Two tags are connected when they appear together on the same article.
    Edge weight = number of articles on which that pair co-occurs.
    Node count  = total number of articles the tag appears in.
    """
    node_counts: dict[str, int] = defaultdict(int)
    node_topics: dict[str, set[str]] = defaultdict(set)
    edge_weights: dict[tuple[str, str], int] = defaultdict(int)

    for result in results:
        topic_title = result.config.title
        for article in result.articles:
            tags = list(dict.fromkeys(article.tags))
            for tag in tags:
                node_counts[tag] += 1
                node_topics[tag].add(topic_title)
            for tag_a, tag_b in combinations(sorted(tags), 2):
                edge_weights[(tag_a, tag_b)] += 1

    nodes = [
        {"id": tag, "count": count, "topics": sorted(node_topics[tag])}
        for tag, count in sorted(node_counts.items(), key=lambda x: -x[1])
    ]
    links = [
        {"source": src, "target": tgt, "weight": w}
        for (src, tgt), w in sorted(edge_weights.items(), key=lambda x: -x[1])
    ]
    return {"nodes": nodes, "links": links}


def find_bridge_tags(
    graph_data: dict[str, Any], min_topics: int = 3, limit: int = 8
) -> list[dict[str, Any]]:
    """
    Return tags that appear across many different topics — structural
    evidence of a cross-cutting theme, independent of any LLM inference.

    A tag whose topics list spans >= min_topics distinct domains is a
    candidate. Results are sorted by topic breadth (most cross-cutting
    first), then by article count, and capped at `limit` so a synthesis
    prompt built from this isn't flooded with long-tail tags.
    """
    bridges = [
        {"tag": n["id"], "topics": n["topics"], "count": n["count"]}
        for n in graph_data["nodes"]
        if len(n["topics"]) >= min_topics
    ]
    bridges.sort(key=lambda b: (-len(b["topics"]), -b["count"]))
    return bridges[:limit]


def build_display_graph(
    full_data: dict[str, Any],
    min_count: int = 3,
    min_weight: int = 2,
) -> dict[str, Any]:
    """
    Prune the full graph and annotate nodes with Louvain community IDs.

    Steps:
      1. Drop edges below min_weight or whose endpoints are below min_count.
      2. Drop nodes with no surviving edges (isolates after pruning).
      3. Run Louvain community detection on the pruned graph.
      4. Label each community by its highest-count member tag.
      5. Return display-ready dicts with community + community_label on each node.
    """
    node_meta = {n["id"]: n for n in full_data["nodes"]}

    kept_links = [
        link for link in full_data["links"]
        if link["weight"] >= min_weight
        and node_meta.get(link["source"], {}).get("count", 0) >= min_count
        and node_meta.get(link["target"], {}).get("count", 0) >= min_count
    ]

    G: nx.Graph[str] = nx.Graph()
    for link in kept_links:
        G.add_edge(link["source"], link["target"], weight=link["weight"])

    # Louvain communities, sorted largest-first so community 0 is the biggest.
    raw_communities = nx.community.louvain_communities(G, seed=42)
    raw_communities = sorted(raw_communities, key=len, reverse=True)
    community_map: dict[str, int] = {
        node: i for i, comm in enumerate(raw_communities) for node in comm
    }

    # Name each community by its highest-count member (for tooltip display).
    community_top: dict[int, str] = {}
    for node_id, comm_id in community_map.items():
        top = community_top.get(comm_id)
        if top is None or node_meta[node_id]["count"] > node_meta[top]["count"]:
            community_top[comm_id] = node_id

    display_nodes = sorted(
        [
            {
                **node_meta[node_id],
                "community": community_map[node_id],
                "community_label": community_top.get(community_map[node_id], ""),
            }
            for node_id in G.nodes()
            if node_id in node_meta
        ],
        key=lambda n: -n["count"],
    )

    return {
        "nodes": display_nodes,
        "links": kept_links,
        "n_communities": len(raw_communities),
        "min_count": min_count,
        "min_weight": min_weight,
        "full_node_count": len(full_data["nodes"]),
        "full_edge_count": len(full_data["links"]),
    }


def group_articles_by_community(
    results: list[TopicResult],
    display_data: dict[str, Any],
) -> dict[int, dict[str, Any]]:
    """
    Group this run's articles by Louvain community (display_data's
    node "community" assignment, from build_display_graph), as the
    grounding material for an LLM-written community summary.

    An article belongs to a community if any of its (normalized) tags is a
    member of that community — an article whose tags span multiple
    communities can appear under more than one. Articles are deduplicated
    by link within a community (the same article can carry two tags that
    both belong to the same community).

    Returns {community_id: {"label": str, "tags": list[str],
    "articles": list[ArticleSummary]}} — only for communities that have at
    least one matching article. Communities in display_data with no
    surviving articles (shouldn't normally happen, since community
    membership itself comes from articles' tags) are simply absent.
    """
    tag_to_community: dict[str, int] = {n["id"]: n["community"] for n in display_data["nodes"]}
    community_label: dict[int, str] = {
        n["community"]: n["community_label"] for n in display_data["nodes"]
    }
    community_tags: dict[int, set[str]] = defaultdict(set)
    for n in display_data["nodes"]:
        community_tags[n["community"]].add(n["id"])

    community_articles: dict[int, list[ArticleSummary]] = defaultdict(list)
    seen_links: dict[int, set[str]] = defaultdict(set)
    for result in results:
        for article in result.articles:
            article_communities = {
                tag_to_community[t] for t in article.tags if t in tag_to_community
            }
            for comm_id in article_communities:
                if article.link not in seen_links[comm_id]:
                    seen_links[comm_id].add(article.link)
                    community_articles[comm_id].append(article)

    return {
        comm_id: {
            "label": community_label[comm_id],
            "tags": sorted(community_tags[comm_id]),
            "articles": articles,
        }
        for comm_id, articles in community_articles.items()
    }


# ---------------------------------------------------------------------------
# D3.js viewer — shared by the standalone tag_graph.html and the inline graph
# in each featured report (renderer.render_report()).
# ---------------------------------------------------------------------------
#
# TAG_GRAPH_CSS / TAG_GRAPH_JS define one self-contained component,
# mountTagGraph(root, data, opts), that builds its own controls inside `root`
# (no page-global element IDs), so a page can host it inline or full-screen.
#
# Two injection points, both handled here rather than at each call site:
#   - The graph JSON is embedded in a <script> block. Tags are LLM output
#     derived from RSS content we don't control, so a tag containing
#     "</script>" must not close the block early -- script_safe_json()
#     escapes <, >, & (same approach as renderer._build_article_jsonld()).
#   - Tooltip/legend text is set via textContent, never innerHTML, so a tag
#     containing markup renders as text.

TAG_GRAPH_D3_URL = "https://d3js.org/d3.v7.min.js"

TAG_GRAPH_CSS = """\
.tag-graph { position: relative; background: #f8f9fa; border: 1px solid #e0e0e0;
             border-radius: 4px; }
.tag-graph .tg-controls { display: flex; gap: 24px; padding: 10px 14px; background: #fff;
             border-bottom: 1px solid #e0e0e0; align-items: center; flex-wrap: wrap;
             font-size: 13px; color: #444; border-radius: 4px 4px 0 0; }
.tag-graph .tg-group { display: flex; align-items: center; gap: 8px; }
.tag-graph .tg-group input[type=range] { width: 120px; accent-color: #4e79a7; }
.tag-graph .tg-val { font-weight: 600; color: #1a1a1a; min-width: 22px; }
.tag-graph .tg-stats { font-size: 12px; color: #888; margin-left: auto; }
.tag-graph .tg-legend { display: flex; flex-wrap: wrap; gap: 6px 14px; padding: 8px 14px;
             font-size: 12px; color: #444; background: #fff; border-bottom: 1px solid #e0e0e0; }
.tag-graph .tg-legend[hidden] { display: none; }
.tag-graph .tg-swatch { display: inline-block; width: 10px; height: 10px; border-radius: 50%;
             margin-right: 5px; vertical-align: middle; }
.tag-graph svg { display: block; width: 100%; height: 100%; cursor: grab; }
.tag-graph svg:active { cursor: grabbing; }
.tag-graph .tg-canvas { height: 560px; }
.tag-graph .link { stroke: #bbb; stroke-opacity: 0.45; }
.tag-graph .node circle { stroke: #fff; stroke-width: 1.5px; cursor: pointer; }
.tag-graph .node circle:hover { stroke: #333; stroke-width: 2px; }
.tag-graph .node text { font-size: 11px; fill: #333; pointer-events: none; }
.tag-graph-tooltip { position: fixed; background: rgba(20,20,20,0.88); color: #fff;
             padding: 8px 12px; border-radius: 6px; font-size: 12px; line-height: 1.7;
             pointer-events: none; opacity: 0; transition: opacity 0.12s;
             max-width: 260px; z-index: 10; }
.tag-graph-tooltip strong { display: block; margin-bottom: 2px; font-size: 13px; }
.tag-graph-tooltip .comm { font-style: italic; color: #ccc; }
"""

TAG_GRAPH_JS = """\
function mountTagGraph(root, allData, opts) {
  opts = opts || {};
  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  };

  // Color palette: Tableau10 + Set3 covers up to 22 communities.
  const palette = d3.schemeTableau10.concat(d3.schemeSet3);
  const MULTI_TOPIC_COLOR = "#555";
  const topicOrder = allData.topic_order || [];
  const topicColor = new Map(topicOrder.map((t, i) => [t, palette[i % palette.length]]));
  let colorMode = "community";
  let circles = null;  // node circles of the current render, recolored on toggle
  const nodeColor = d => {
    if (colorMode === "topic") {
      return d.topics.length === 1 ? (topicColor.get(d.topics[0]) || "#999") : MULTI_TOPIC_COLOR;
    }
    return palette[d.community % palette.length];
  };

  // --- controls -----------------------------------------------------------
  const controls = el("div", "tg-controls");
  const slider = (label, value) => {
    const group = el("div", "tg-group");
    const lab = el("label", null, label);
    const input = el("input");
    input.type = "range"; input.min = "1"; input.value = String(value);
    const val = el("span", "tg-val", String(value));
    lab.appendChild(input);
    group.append(lab, val);
    controls.appendChild(group);
    return { input, val };
  };
  const maxWeight = d3.max(allData.links, d => d.weight) || 20;
  const maxCount  = d3.max(allData.nodes, d => d.count)  || 30;
  const weight = slider("Min co-occurrences ", allData.min_weight ?? 2);
  const count  = slider("Min article count ", allData.min_count ?? 3);
  weight.input.max = String(Math.max(20, maxWeight));
  count.input.max  = String(Math.max(30, maxCount));

  let legend = null;
  if (opts.topicToggle && topicOrder.length) {
    const group = el("div", "tg-group");
    const lab = el("label", null, "Color by ");
    const select = el("select");
    [["community", "community cluster"], ["topic", "topic"]].forEach(([v, t]) => {
      const o = el("option", null, t); o.value = v; select.appendChild(o);
    });
    lab.appendChild(select);
    group.appendChild(lab);
    controls.appendChild(group);

    legend = el("div", "tg-legend");
    legend.hidden = true;
    const entry = (color, label) => {
      const item = el("span");
      const sw = el("span", "tg-swatch"); sw.style.background = color;
      item.append(sw, document.createTextNode(label));
      legend.appendChild(item);
    };
    topicOrder.forEach(t => entry(topicColor.get(t), t));
    entry(MULTI_TOPIC_COLOR, "Multiple topics");

    select.addEventListener("change", () => {
      colorMode = select.value;
      legend.hidden = colorMode !== "topic";
      circles && circles.attr("fill", nodeColor);
    });
  }
  const stats = el("div", "tg-stats");
  controls.appendChild(stats);

  const canvas = el("div", "tg-canvas");
  const svgEl = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  canvas.appendChild(svgEl);
  root.appendChild(controls);
  if (legend) root.appendChild(legend);
  root.appendChild(canvas);

  const tooltipEl = el("div", "tag-graph-tooltip");
  document.body.appendChild(tooltipEl);
  const tooltip = d3.select(tooltipEl);

  function render() {
    const minWeight = +weight.input.value;
    const minCount  = +count.input.value;

    const nodes = allData.nodes.filter(n => n.count >= minCount);
    const nodeIds = new Set(nodes.map(n => n.id));
    const links = allData.links.filter(l =>
      l.weight >= minWeight && nodeIds.has(l.source) && nodeIds.has(l.target)
    );
    const connected = new Set(links.flatMap(l => [l.source, l.target]));
    const visNodes = nodes.filter(n => connected.has(n.id));

    const nComm = new Set(visNodes.map(n => n.community)).size;
    stats.textContent =
      `${visNodes.length} tags · ${links.length} connections · ${nComm} communities`;

    const svg = d3.select(svgEl);
    svg.selectAll("*").remove();
    const { width, height } = svgEl.getBoundingClientRect();
    const g = svg.append("g");
    svg.call(d3.zoom().scaleExtent([0.05, 10]).on("zoom", e => g.attr("transform", e.transform)));

    const r  = d => Math.max(5, Math.sqrt(d.count) * 5);
    const lw = d => Math.max(0.6, Math.sqrt(d.weight) * 1.4);

    const simNodes = visNodes.map(n => ({ ...n }));
    const simLinks = links.map(l => ({ ...l }));
    const sim = d3.forceSimulation(simNodes)
      .force("link", d3.forceLink(simLinks).id(d => d.id)
        .distance(d => Math.max(40, 90 / Math.sqrt(d.weight))))
      .force("charge", d3.forceManyBody().strength(-180))
      .force("center", d3.forceCenter(width / 2, height / 2))
      .force("collide", d3.forceCollide().radius(d => r(d) + 4));

    const linkSel = g.append("g").selectAll("line")
      .data(sim.force("link").links()).join("line")
        .attr("class", "link").attr("stroke-width", lw);

    const nodeSel = g.append("g").selectAll("g")
      .data(simNodes).join("g")
        .attr("class", "node")
        .call(d3.drag()
          .on("start", (e, d) => { if (!e.active) sim.alphaTarget(0.3).restart(); \
d.fx = d.x; d.fy = d.y; })
          .on("drag",  (e, d) => { d.fx = e.x; d.fy = e.y; })
          .on("end",   (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }))
        .on("mouseover", (e, d) => {
          // Built with textContent, never innerHTML: tags are untrusted text.
          tooltipEl.replaceChildren(
            el("strong", null, d.id),
            document.createTextNode(`Articles: ${d.count}`), el("br"),
            document.createTextNode(`Topics: ${d.topics.join(", ")}`), el("br"),
            el("span", "comm", `Cluster: ${d.community_label} (${d.community})`)
          );
          tooltip.style("opacity", 1);
        })
        .on("mousemove", e => {
          tooltip.style("left", (e.clientX + 14) + "px").style("top", (e.clientY - 42) + "px");
        })
        .on("mouseout", () => tooltip.style("opacity", 0));

    circles = nodeSel.append("circle").attr("r", r).attr("fill", nodeColor);

    nodeSel.append("text")
      .attr("x", d => r(d) + 3)
      .attr("y", "0.35em")
      .attr("font-size", d => Math.min(13, 8 + r(d) * 0.25) + "px")
      .text(d => d.count >= 4 ? d.id : "");

    sim.on("tick", () => {
      linkSel
        .attr("x1", d => d.source.x).attr("y1", d => d.source.y)
        .attr("x2", d => d.target.x).attr("y2", d => d.target.y);
      nodeSel.attr("transform", d => `translate(${d.x},${d.y})`);
    });
  }

  [weight, count].forEach(s => s.input.addEventListener("input", () => {
    s.val.textContent = s.input.value;
    render();
  }));
  render();
}
"""


def script_safe_json(data: Any) -> str:
    """
    json.dumps() for embedding inside an HTML <script> block.

    The JSON spec allows literal <, >, & inside strings, so a value like
    "</script>" would close the surrounding block early. Escaping them as
    \\uXXXX keeps the JSON semantically identical while making a breakout
    impossible. Tags here come from LLM output over RSS content we don't
    control -- same threat model as renderer._build_article_jsonld().
    """
    return (
        json.dumps(data, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def build_subset_display_graph(
    results: list[TopicResult],
    topic_order: list[str],
    min_count: int = 3,
    min_weight: int = 2,
) -> dict[str, Any]:
    """
    Display graph over just `results` (e.g. one featured report's topics),
    with Louvain communities recomputed on that subset -- so clusters and
    colors are specific to these topics and won't match the full
    tag_graph.html. topic_order (topic titles) fixes the order of the
    viewer's color-by-topic legend.
    """
    display = build_display_graph(
        build_graph_data(results), min_count=min_count, min_weight=min_weight
    )
    display["topic_order"] = topic_order
    return display


_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Tag Network — Strategic Reports</title>
  <script src="__D3_URL__"></script>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; \
background: #f8f9fa; }

    #header { padding: 16px 24px; background: #fff; border-bottom: 1px solid #e0e0e0; }
    #header h1 { font-size: 18px; font-weight: 600; color: #1a1a1a; }
    #header p  { font-size: 13px; color: #666; margin-top: 4px; }

__CSS__
    /* Full-screen variant: no frame, canvas fills the viewport below the header. */
    #graph-root.tag-graph { border: none; border-radius: 0; }
    #graph-root .tg-controls { padding: 12px 24px; border-radius: 0; }
    #graph-root .tg-canvas { height: calc(100vh - 108px); }
  </style>
</head>
<body>

<div id="header">
  <h1>Tag Network Graph</h1>
  <p>
    Tags co-occurring in the same article are connected. Node color = Louvain community cluster.
    Node size = article count &nbsp;·&nbsp; Edge thickness = co-occurrence count.
    Scroll to zoom &nbsp;·&nbsp; Drag to pan &nbsp;·&nbsp; Drag nodes to reposition.
  </p>
</div>

<div id="graph-root" class="tag-graph"></div>

<script>
__JS__
mountTagGraph(document.getElementById("graph-root"), __GRAPH_DATA__, {});
</script>
</body>
</html>
"""


def write_tag_graph(
    results: list[TopicResult],
    output_dir: Path,
    min_count: int = 3,
    min_weight: int = 2,
) -> None:
    """
    Write tag_graph.json (full), tag_graph_display.json (pruned+communities),
    and tag_graph.html into output_dir.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    full_data = build_graph_data(results)
    display_data = build_display_graph(full_data, min_count=min_count, min_weight=min_weight)

    (output_dir / "tag_graph.json").write_text(
        json.dumps(full_data, indent=2, ensure_ascii=False)
    )
    (output_dir / "tag_graph_display.json").write_text(
        json.dumps(display_data, indent=2, ensure_ascii=False)
    )
    # Placeholders substituted in one pass order that can't collide: the graph
    # data goes last, so text inside it is never re-scanned for __JS__ etc.
    html = (
        _HTML.replace("__D3_URL__", TAG_GRAPH_D3_URL)
        .replace("__CSS__", TAG_GRAPH_CSS)
        .replace("__JS__", TAG_GRAPH_JS)
        .replace("__GRAPH_DATA__", script_safe_json(display_data))
    )
    (output_dir / "tag_graph.html").write_text(html)
