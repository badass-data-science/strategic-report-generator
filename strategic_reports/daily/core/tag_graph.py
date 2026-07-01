"""
Tag co-occurrence network graph generator.

Two outputs:
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

import networkx as nx

from .models import TopicResult


def build_graph_data(results: list[TopicResult]) -> dict:
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


def build_display_graph(
    full_data: dict,
    min_count: int = 3,
    min_weight: int = 2,
) -> dict:
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
        l for l in full_data["links"]
        if l["weight"] >= min_weight
        and node_meta.get(l["source"], {}).get("count", 0) >= min_count
        and node_meta.get(l["target"], {}).get("count", 0) >= min_count
    ]

    G = nx.Graph()
    for l in kept_links:
        G.add_edge(l["source"], l["target"], weight=l["weight"])

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


# ---------------------------------------------------------------------------
# D3.js HTML viewer — loads tag_graph_display.json, colors by community
# ---------------------------------------------------------------------------

_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Tag Network — Strategic Reports</title>
  <script src="https://d3js.org/d3.v7.min.js"></script>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f8f9fa; }

    #header { padding: 16px 24px; background: #fff; border-bottom: 1px solid #e0e0e0; }
    #header h1 { font-size: 18px; font-weight: 600; color: #1a1a1a; }
    #header p  { font-size: 13px; color: #666; margin-top: 4px; }

    #controls {
      display: flex; gap: 32px; padding: 12px 24px;
      background: #fff; border-bottom: 1px solid #e0e0e0;
      align-items: center; flex-wrap: wrap;
    }
    .control-group { display: flex; align-items: center; gap: 10px; font-size: 13px; color: #444; }
    .control-group input[type=range] { width: 140px; accent-color: #4e79a7; }
    .control-group .val { font-weight: 600; color: #1a1a1a; min-width: 24px; }
    #stats { font-size: 12px; color: #888; margin-left: auto; }

    #graph-container { width: 100%; height: calc(100vh - 108px); }
    svg { width: 100%; height: 100%; cursor: grab; }
    svg:active { cursor: grabbing; }

    .link { stroke: #bbb; stroke-opacity: 0.45; }
    .node circle { stroke: #fff; stroke-width: 1.5px; cursor: pointer; }
    .node circle:hover { stroke: #333; stroke-width: 2px; }
    .node text { font-size: 11px; fill: #333; pointer-events: none; }

    #tooltip {
      position: fixed; background: rgba(20,20,20,0.88); color: #fff;
      padding: 8px 12px; border-radius: 6px; font-size: 12px; line-height: 1.7;
      pointer-events: none; opacity: 0; transition: opacity 0.12s;
      max-width: 260px; z-index: 10;
    }
    #tooltip strong { display: block; margin-bottom: 2px; font-size: 13px; }
    #tooltip .comm { font-style: italic; color: #ccc; }
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

<div id="controls">
  <div class="control-group">
    <label for="weight-filter">Min co-occurrences</label>
    <input type="range" id="weight-filter" min="1" value="2">
    <span class="val" id="weight-val">2</span>
  </div>
  <div class="control-group">
    <label for="count-filter">Min article count</label>
    <input type="range" id="count-filter" min="1" value="3">
    <span class="val" id="count-val">3</span>
  </div>
  <div id="stats"></div>
</div>

<div id="graph-container">
  <svg id="graph"></svg>
</div>
<div id="tooltip"></div>

<script>
// Color palette: Tableau10 + Set3 covers up to 22 communities.
const palette = d3.schemeTableau10.concat(d3.schemeSet3);
const communityColor = d => palette[d.community % palette.length];
const tooltip = d3.select("#tooltip");

const allData = __GRAPH_DATA__;

(function init() {
  const maxWeight = d3.max(allData.links, d => d.weight) || 20;
  const maxCount  = d3.max(allData.nodes, d => d.count)  || 30;
  document.getElementById("weight-filter").max = Math.max(20, maxWeight);
  document.getElementById("count-filter").max  = Math.max(30, maxCount);

  // Set slider defaults to the thresholds used at build time.
  document.getElementById("weight-filter").value = allData.min_weight ?? 2;
  document.getElementById("weight-val").textContent = allData.min_weight ?? 2;
  document.getElementById("count-filter").value = allData.min_count ?? 3;
  document.getElementById("count-val").textContent = allData.min_count ?? 3;

  render();
})();

function filters() {
  return {
    minWeight: +document.getElementById("weight-filter").value,
    minCount:  +document.getElementById("count-filter").value,
  };
}

function render() {
  const { minWeight, minCount } = filters();

  const nodes = allData.nodes.filter(n => n.count >= minCount);
  const nodeIds = new Set(nodes.map(n => n.id));

  const links = allData.links.filter(l =>
    l.weight >= minWeight &&
    nodeIds.has(l.source.id ?? l.source) &&
    nodeIds.has(l.target.id ?? l.target)
  );

  const connected = new Set(
    links.flatMap(l => [l.source.id ?? l.source, l.target.id ?? l.target])
  );
  const visNodes = nodes.filter(n => connected.has(n.id));

  const nComm = new Set(visNodes.map(n => n.community)).size;
  document.getElementById("stats").textContent =
    `${visNodes.length} tags · ${links.length} connections · ${nComm} communities`;

  const svg = d3.select("#graph");
  svg.selectAll("*").remove();

  const { width, height } = svg.node().getBoundingClientRect();
  const g = svg.append("g");
  svg.call(
    d3.zoom().scaleExtent([0.05, 10])
      .on("zoom", e => g.attr("transform", e.transform))
  );

  const r  = d => Math.max(5, Math.sqrt(d.count) * 5);
  const lw = d => Math.max(0.6, Math.sqrt(d.weight) * 1.4);

  const simNodes = visNodes.map(n => ({ ...n }));
  const simLinks = links.map(l => ({ ...l }));

  const sim = d3.forceSimulation(simNodes)
    .force("link", d3.forceLink(simLinks)
      .id(d => d.id)
      .distance(d => Math.max(40, 90 / Math.sqrt(d.weight)))
    )
    .force("charge", d3.forceManyBody().strength(-180))
    .force("center", d3.forceCenter(width / 2, height / 2))
    .force("collide", d3.forceCollide().radius(d => r(d) + 4));

  const linkSel = g.append("g")
    .selectAll("line")
    .data(sim.force("link").links())
    .join("line")
      .attr("class", "link")
      .attr("stroke-width", lw);

  const nodeSel = g.append("g")
    .selectAll("g")
    .data(simNodes)
    .join("g")
      .attr("class", "node")
      .call(
        d3.drag()
          .on("start", (e, d) => { if (!e.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
          .on("drag",  (e, d) => { d.fx = e.x; d.fy = e.y; })
          .on("end",   (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; })
      )
      .on("mouseover", (e, d) => {
        tooltip.style("opacity", 1).html(
          `<strong>${d.id}</strong>` +
          `Articles: ${d.count}<br>` +
          `Topics: ${d.topics.join(", ")}<br>` +
          `<span class="comm">Cluster: ${d.community_label} (${d.community})</span>`
        );
      })
      .on("mousemove", e => {
        tooltip.style("left", (e.clientX + 14) + "px").style("top", (e.clientY - 42) + "px");
      })
      .on("mouseout", () => tooltip.style("opacity", 0));

  nodeSel.append("circle")
    .attr("r", r)
    .attr("fill", communityColor);

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

["weight-filter", "count-filter"].forEach(id => {
  const slider = document.getElementById(id);
  const valEl  = document.getElementById(id.replace("filter", "val"));
  slider.addEventListener("input", () => {
    valEl.textContent = slider.value;
    if (allData) render();
  });
});
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
    display_json = json.dumps(display_data, ensure_ascii=False)
    (output_dir / "tag_graph_display.json").write_text(
        json.dumps(display_data, indent=2, ensure_ascii=False)
    )
    (output_dir / "tag_graph.html").write_text(
        _HTML.replace("__GRAPH_DATA__", display_json)
    )
