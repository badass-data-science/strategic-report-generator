"""
Tag co-occurrence network graph generator.

Builds a graph where:
  - Nodes  = normalized tags; sized by article count
  - Edges  = pairs of tags that co-appear on the same article; weighted by
             co-occurrence count

Output:
  tag_graph.json  — raw graph data (nodes + links) consumed by the HTML file
  tag_graph.html  — self-contained D3.js force-directed interactive graph

The HTML loads tag_graph.json via fetch(), so both files must be served from
the same directory (which they are — both land in output_dir and are uploaded
together by the SCP step).
"""

import json
from collections import defaultdict
from itertools import combinations
from pathlib import Path

from .models import TopicResult


def build_graph_data(results: list[TopicResult]) -> dict:
    """
    Build nodes and weighted edges from tag co-occurrence across all articles.

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
            tags = list(dict.fromkeys(article.tags))  # deduplicate, preserve order
            for tag in tags:
                node_counts[tag] += 1
                node_topics[tag].add(topic_title)
            # All unique tag pairs within this article contribute one co-occurrence.
            for tag_a, tag_b in combinations(sorted(tags), 2):
                edge_weights[(tag_a, tag_b)] += 1

    nodes = [
        {
            "id": tag,
            "count": count,
            "topics": sorted(node_topics[tag]),
        }
        for tag, count in sorted(node_counts.items(), key=lambda x: -x[1])
    ]

    links = [
        {"source": src, "target": tgt, "weight": w}
        for (src, tgt), w in sorted(edge_weights.items(), key=lambda x: -x[1])
    ]

    return {"nodes": nodes, "links": links}


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

    .link { stroke: #bbb; stroke-opacity: 0.55; }
    .node circle { stroke: #fff; stroke-width: 1.5px; cursor: pointer; transition: opacity 0.15s; }
    .node circle:hover { stroke: #333; stroke-width: 2px; }
    .node text { font-size: 11px; fill: #333; pointer-events: none; }

    #tooltip {
      position: fixed; background: rgba(20,20,20,0.88); color: #fff;
      padding: 8px 12px; border-radius: 6px; font-size: 12px; line-height: 1.6;
      pointer-events: none; opacity: 0; transition: opacity 0.12s;
      max-width: 240px; z-index: 10;
    }
    #tooltip strong { display: block; margin-bottom: 2px; font-size: 13px; }
  </style>
</head>
<body>

<div id="header">
  <h1>Tag Network Graph</h1>
  <p>
    Tags co-occurring in the same article are connected.
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
    <input type="range" id="count-filter" min="1" value="2">
    <span class="val" id="count-val">2</span>
  </div>
  <div id="stats"></div>
</div>

<div id="graph-container">
  <svg id="graph"></svg>
</div>
<div id="tooltip"></div>

<script>
// One color per topic (Tableau10 + two extras for up to 12 topics).
const topicColor = d3.scaleOrdinal(
  d3.schemeTableau10.concat(["#9467bd", "#8c564b"])
);
const tooltip = d3.select("#tooltip");

let allData = null;

fetch("tag_graph.json")
  .then(r => r.json())
  .then(data => {
    allData = data;
    // Set slider maxes from actual data ranges.
    const maxWeight = d3.max(data.links, d => d.weight) || 20;
    const maxCount  = d3.max(data.nodes, d => d.count)  || 30;
    document.getElementById("weight-filter").max = Math.max(20, maxWeight);
    document.getElementById("count-filter").max  = Math.max(30, maxCount);
    render();
  });

function filters() {
  return {
    minWeight: +document.getElementById("weight-filter").value,
    minCount:  +document.getElementById("count-filter").value,
  };
}

function render() {
  const { minWeight, minCount } = filters();

  // Filter nodes by article count.
  const nodes = allData.nodes.filter(n => n.count >= minCount);
  const nodeIds = new Set(nodes.map(n => n.id));

  // Filter links: both endpoints must survive the node filter and meet weight threshold.
  const links = allData.links.filter(l =>
    l.weight >= minWeight &&
    nodeIds.has(l.source.id ?? l.source) &&
    nodeIds.has(l.target.id ?? l.target)
  );

  // Drop isolated nodes (no surviving edges).
  const connected = new Set(
    links.flatMap(l => [l.source.id ?? l.source, l.target.id ?? l.target])
  );
  const visNodes = nodes.filter(n => connected.has(n.id));

  document.getElementById("stats").textContent =
    `${visNodes.length} tags · ${links.length} connections`;

  // --- Build the SVG ---
  const svg = d3.select("#graph");
  svg.selectAll("*").remove();

  const { width, height } = svg.node().getBoundingClientRect();

  const g = svg.append("g");
  svg.call(
    d3.zoom().scaleExtent([0.08, 8])
      .on("zoom", e => g.attr("transform", e.transform))
  );

  const r     = d => Math.max(5, Math.sqrt(d.count) * 5);
  const lw    = d => Math.max(0.8, Math.sqrt(d.weight) * 1.6);

  // Deep-copy so D3 can mutate x/y freely without corrupting allData.
  const simNodes = visNodes.map(n => ({ ...n }));
  const simLinks = links.map(l => ({ ...l }));

  const sim = d3.forceSimulation(simNodes)
    .force("link", d3.forceLink(simLinks)
      .id(d => d.id)
      .distance(d => Math.max(40, 100 / Math.sqrt(d.weight)))
    )
    .force("charge", d3.forceManyBody().strength(-150))
    .force("center", d3.forceCenter(width / 2, height / 2))
    .force("collide", d3.forceCollide().radius(d => r(d) + 5));

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
          `Topics: ${d.topics.join(", ")}`
        );
      })
      .on("mousemove", e => {
        tooltip
          .style("left", (e.clientX + 14) + "px")
          .style("top",  (e.clientY - 36) + "px");
      })
      .on("mouseout", () => tooltip.style("opacity", 0));

  nodeSel.append("circle")
    .attr("r", r)
    .attr("fill", d => topicColor(d.topics[0] ?? "unknown"));

  // Label nodes that appear in enough articles; others show only on hover via tooltip.
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

// Wire up sliders.
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


def write_tag_graph(results: list[TopicResult], output_dir: Path) -> None:
    """Write tag_graph.json and tag_graph.html into output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)

    graph_data = build_graph_data(results)

    (output_dir / "tag_graph.json").write_text(
        json.dumps(graph_data, indent=2, ensure_ascii=False)
    )
    (output_dir / "tag_graph.html").write_text(_HTML)
