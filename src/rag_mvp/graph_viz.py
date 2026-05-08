"""Generate a standalone D3.js force-directed graph from LightRAG's graphml file."""

from __future__ import annotations

import json
import webbrowser
import xml.etree.ElementTree as ET
from pathlib import Path

from .config import settings

_GRAPHML_NS = "http://graphml.graphdrawing.org/xmlns"

_TYPE_COLOURS = {
    "concept": "#4e79a7",
    "organization": "#f28e2b",
    "person": "#e15759",
    "artifact": "#76b7b2",
    "method": "#59a14f",
    "event": "#edc948",
    "data": "#b07aa1",
    "location": "#ff9da7",
    "technology": "#9c755f",
}
_DEFAULT_COLOUR = "#aaaaaa"


def _parse_graphml(graphml_path: Path) -> tuple[list[dict], list[dict]]:
    tree = ET.parse(graphml_path)
    root = tree.getroot()
    ns = {"g": _GRAPHML_NS}
    key_map: dict[str, str] = {}
    for key_el in root.findall("g:key", ns):
        key_map[key_el.get("id", "")] = key_el.get("attr.name", "")
    graph_el = root.find("g:graph", ns)
    if graph_el is None:
        return [], []
    nodes: list[dict] = []
    edges: list[dict] = []
    for node_el in graph_el.findall("g:node", ns):
        node_id = node_el.get("id", "")
        data: dict[str, str] = {}
        for data_el in node_el.findall("g:data", ns):
            k = key_map.get(data_el.get("key", ""), "")
            if k:
                data[k] = data_el.text or ""
        desc = data.get("description", "").split("<SEP>")[0].strip()
        nodes.append({
            "id": node_id,
            "entity_type": data.get("entity_type", ""),
            "description": desc,
            "file_path": data.get("file_path", ""),
        })
    for idx, edge_el in enumerate(graph_el.findall("g:edge", ns)):
        data = {}
        for data_el in edge_el.findall("g:data", ns):
            k = key_map.get(data_el.get("key", ""), "")
            if k:
                data[k] = data_el.text or ""
        try:
            weight = float(data.get("weight", "1.0"))
        except ValueError:
            weight = 1.0
        edges.append({
            "id": idx,
            "source": edge_el.get("source", ""),
            "target": edge_el.get("target", ""),
            "weight": weight,
            "description": data.get("description", "").split("<SEP>")[0].strip(),
            "keywords": data.get("keywords", ""),
        })
    return nodes, edges


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"/>
<title>知识图谱可视化</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: "Segoe UI", sans-serif; background: #1a1a2e; color: #e0e0e0; height: 100vh; display: flex; flex-direction: column; }
#toolbar { display: flex; align-items: center; gap: 12px; padding: 8px 16px; background: #16213e; border-bottom: 1px solid #0f3460; flex-shrink: 0; flex-wrap: wrap; }
#toolbar h1 { font-size: 15px; font-weight: 600; color: #e94560; margin-right: auto; }
#search { padding: 4px 10px; border-radius: 4px; border: 1px solid #0f3460; background: #1a1a2e; color: #e0e0e0; width: 220px; }
label { font-size: 12px; color: #aaa; }
input[type=range] { width: 90px; }
#stats { font-size: 11px; color: #888; }
#container { flex: 1; overflow: hidden; position: relative; }
svg { width: 100%; height: 100%; }
.node circle { stroke-width: 1.5px; cursor: pointer; }
.node circle:hover { stroke: #fff; }
.node text { font-size: 11px; fill: #e0e0e0; pointer-events: none; text-shadow: 0 0 3px #000; }
.link { stroke-opacity: 0.35; }
.link:hover { stroke-opacity: 1; }
#tooltip {
  position: absolute; background: rgba(22,33,62,.95); border: 1px solid #0f3460;
  border-radius: 6px; padding: 10px 14px; font-size: 12px; max-width: 300px;
  pointer-events: none; display: none; z-index: 10; line-height: 1.6;
}
#tooltip b { color: #e94560; }
#legend { display: flex; flex-wrap: wrap; gap: 6px; padding: 4px 16px 6px; background: #16213e; }
.legend-item { display: flex; align-items: center; gap: 4px; font-size: 11px; color: #ccc; }
.legend-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
</style>
</head>
<body>
<div id="toolbar">
  <h1>🕸 知识图谱</h1>
  <input id="search" type="text" placeholder="搜索节点…"/>
  <label>斥力 <input id="charge" type="range" min="-800" max="-10" value="-200"/></label>
  <label>连接距离 <input id="linkdist" type="range" min="20" max="300" value="80"/></label>
  <span id="stats"></span>
</div>
<div id="legend"></div>
<div id="container">
  <svg id="svg"></svg>
  <div id="tooltip"></div>
</div>
<script src="https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js"></script>
<script>
const GRAPH = GRAPH_JSON_PLACEHOLDER;
const typeColour = TYPE_COLOUR_JSON_PLACEHOLDER;
const defaultColour = "DEFAULT_COLOUR_PLACEHOLDER";

const legendEl = document.getElementById("legend");
const usedTypes = [...new Set(GRAPH.nodes.map(n => n.entity_type).filter(Boolean))];
usedTypes.forEach(t => {
  const item = document.createElement("div");
  item.className = "legend-item";
  item.innerHTML = `<div class="legend-dot" style="background:${typeColour[t]||defaultColour}"></div>${t}`;
  legendEl.appendChild(item);
});

const container = document.getElementById("container");
const tooltip   = document.getElementById("tooltip");
const svg       = d3.select("#svg");
const stats     = document.getElementById("stats");
stats.textContent = `${GRAPH.nodes.length} 节点  ${GRAPH.edges.length} 边`;

let width = container.clientWidth, height = container.clientHeight;
const zoom = d3.zoom().scaleExtent([0.02, 10]).on("zoom", e => g.attr("transform", e.transform));
svg.call(zoom);
const g = svg.append("g");

const degMap = {};
GRAPH.edges.forEach(e => {
  degMap[e.source] = (degMap[e.source]||0)+1;
  degMap[e.target] = (degMap[e.target]||0)+1;
});
GRAPH.nodes.forEach(n => { n._deg = degMap[n.id]||0; });
const maxDeg = d3.max(GRAPH.nodes, d => d._deg) || 1;
const sizeScale = d3.scaleSqrt().domain([0, maxDeg]).range([4, 22]);

const simulation = d3.forceSimulation(GRAPH.nodes)
  .force("link", d3.forceLink(GRAPH.edges).id(d => d.id).distance(+document.getElementById("linkdist").value))
  .force("charge", d3.forceManyBody().strength(+document.getElementById("charge").value))
  .force("center", d3.forceCenter(width/2, height/2))
  .force("collision", d3.forceCollide(d => sizeScale(d._deg)+3));

const edgeStroke = d3.scaleLinear().domain([0,10]).range([0.8,5]).clamp(true);
const link = g.append("g").selectAll("line")
  .data(GRAPH.edges).enter().append("line")
  .attr("class","link").attr("stroke","#4a6fa5")
  .attr("stroke-width", d => edgeStroke(d.weight))
  .on("mouseover",(e,d)=>showTip(e,`<b>关系</b><br>${d.source.id||d.source} → ${d.target.id||d.target}<br>${d.keywords?"<b>关键词:</b> "+d.keywords+"<br>":""}${d.description||""}`))
  .on("mouseout",hideTip);

const node = g.append("g").selectAll("g")
  .data(GRAPH.nodes).enter().append("g").attr("class","node")
  .call(d3.drag()
    .on("start",(e,d)=>{if(!e.active)simulation.alphaTarget(0.3).restart();d.fx=d.x;d.fy=d.y;})
    .on("drag", (e,d)=>{d.fx=e.x;d.fy=e.y;})
    .on("end",  (e,d)=>{if(!e.active)simulation.alphaTarget(0);}));

node.append("circle")
  .attr("r", d=>sizeScale(d._deg))
  .attr("fill", d=>typeColour[d.entity_type]||defaultColour)
  .attr("stroke", d=>d3.color(typeColour[d.entity_type]||defaultColour).darker(0.8))
  .on("mouseover",(e,d)=>showTip(e,`<b>${d.id}</b><br>${d.entity_type?"<b>类型:</b> "+d.entity_type+"<br>":""}${d.file_path?"<b>来源:</b> "+d.file_path+"<br>":""}${d.description||""}`))
  .on("mouseout",hideTip)
  .on("dblclick",(e,d)=>{e.stopPropagation();if(d.fx!==undefined){delete d.fx;delete d.fy;}else{d.fx=d.x;d.fy=d.y;}});

node.append("text").text(d=>d.id)
  .attr("dx",d=>sizeScale(d._deg)+3).attr("dy","0.35em");

simulation.on("tick",()=>{
  link.attr("x1",d=>d.source.x).attr("y1",d=>d.source.y)
      .attr("x2",d=>d.target.x).attr("y2",d=>d.target.y);
  node.attr("transform",d=>`translate(${d.x},${d.y})`);
});

document.getElementById("charge").addEventListener("input",function(){simulation.force("charge").strength(+this.value);simulation.alpha(0.3).restart();});
document.getElementById("linkdist").addEventListener("input",function(){simulation.force("link").distance(+this.value);simulation.alpha(0.3).restart();});
document.getElementById("search").addEventListener("input",function(){
  const q=this.value.toLowerCase().trim();
  node.select("circle").attr("opacity",d=>!q||d.id.toLowerCase().includes(q)?1:0.1);
  node.select("text").attr("opacity",d=>!q||d.id.toLowerCase().includes(q)?1:0.1);
});

function showTip(e,html){tooltip.innerHTML=html;tooltip.style.display="block";moveTip(e);}
function hideTip(){tooltip.style.display="none";}
function moveTip(e){tooltip.style.left=(e.offsetX+14)+"px";tooltip.style.top=(e.offsetY-10)+"px";}
svg.on("mousemove",moveTip);

new ResizeObserver(()=>{
  width=container.clientWidth;height=container.clientHeight;
  simulation.force("center",d3.forceCenter(width/2,height/2)).alpha(0.1).restart();
}).observe(container);
</script>
</body>
</html>
"""


def build_graph_html(
    graphml_path: Path | None = None,
    output_html: Path | None = None,
    max_nodes: int = 500,
) -> Path:
    if graphml_path is None:
        graphml_path = settings.working_dir / "graph_chunk_entity_relation.graphml"
    if not graphml_path.exists():
        raise FileNotFoundError(
            f"Graph file not found: {graphml_path}\n"
            "Run 'rag ingest' or 'rag reindex' first."
        )
    nodes, edges = _parse_graphml(graphml_path)
    if not nodes:
        raise ValueError("Graph is empty – ingest some documents first.")

    if len(nodes) > max_nodes:
        deg: dict[str, int] = {}
        for e in edges:
            deg[e["source"]] = deg.get(e["source"], 0) + 1
            deg[e["target"]] = deg.get(e["target"], 0) + 1
        nodes.sort(key=lambda n: deg.get(n["id"], 0), reverse=True)
        kept = {n["id"] for n in nodes[:max_nodes]}
        nodes = nodes[:max_nodes]
        edges = [e for e in edges if e["source"] in kept and e["target"] in kept]

    html = _HTML_TEMPLATE.replace(
        "GRAPH_JSON_PLACEHOLDER",
        json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False)
    ).replace(
        "TYPE_COLOUR_JSON_PLACEHOLDER",
        json.dumps(_TYPE_COLOURS, ensure_ascii=False)
    ).replace(
        "DEFAULT_COLOUR_PLACEHOLDER",
        _DEFAULT_COLOUR
    )

    if output_html is None:
        output_html = settings.working_dir / "graph_viz.html"
    output_html.write_text(html, encoding="utf-8")
    return output_html


def open_graph(
    graphml_path: Path | None = None,
    output_html: Path | None = None,
    max_nodes: int = 500,
    no_browser: bool = False,
) -> Path:
    html_path = build_graph_html(graphml_path, output_html, max_nodes)
    if not no_browser:
        webbrowser.open(html_path.resolve().as_uri())
    return html_path