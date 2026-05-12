import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { UserRole } from "@prisma/client";
import { ApiError } from "@/lib/http/api-error";
import { requireAuthenticated } from "@/lib/admin";
import { getAuthFromRequest } from "@/lib/request-auth";
import { getCourseIfMember } from "@/lib/course-access";
import { prisma } from "@/lib/db";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ courseId: string }> };

/** Only allow lowercase letters, digits and underscores (max 63 chars) in table names. */
const TABLE_NAME_RE = /^[a-z][a-z0-9_]{1,62}$/;

function validateTableName(name: string): string {
  const lower = name.toLowerCase();
  if (!TABLE_NAME_RE.test(lower)) {
    throw new ApiError(500, "INTERNAL_ERROR", "Invalid LightRAG table name configuration");
  }
  return lower;
}

function buildGraphHtml(
  courseName: string,
  nodes: { id: string; description: string }[],
  edges: { source: string; target: string; keywords: string }[],
): string {
  const graphJson = JSON.stringify({ nodes, edges });

  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"/>
<title>${courseName.replace(/</g, "&lt;")} — 知识图谱</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; background: #f8f9fb; color: #333; height: 100vh; display: flex; flex-direction: column; overflow: hidden; }
#toolbar { display: flex; align-items: center; gap: 10px; padding: 7px 14px; background: #fff; border-bottom: 1px solid #e5e7eb; flex-shrink: 0; }
#toolbar-title { font-size: 13px; font-weight: 600; color: #374151; }
#search { padding: 4px 10px; border-radius: 6px; border: 1px solid #d1d5db; background: #f9fafb; color: #374151; font-size: 12px; width: 200px; }
#search:focus { outline: none; border-color: #4e79a7; box-shadow: 0 0 0 2px rgba(78,121,167,0.15); }
#stats { font-size: 11px; color: #9ca3af; margin-left: auto; white-space: nowrap; }
#container { flex: 1; overflow: hidden; position: relative; }
svg { width: 100%; height: 100%; }
.node circle { cursor: pointer; stroke-width: 1.5px; transition: opacity 0.12s; }
.node circle:hover { stroke: #1d4ed8; stroke-width: 2px; }
.node text { font-size: 11px; fill: #374151; pointer-events: none; user-select: none; }
.link { stroke: #c3ccd8; stroke-opacity: 0.7; }
#tooltip {
  position: absolute; background: #fff; border: 1px solid #e5e7eb;
  border-radius: 8px; padding: 9px 13px; font-size: 12px; max-width: 280px;
  pointer-events: none; display: none; z-index: 10; line-height: 1.65;
  box-shadow: 0 4px 16px rgba(0,0,0,0.10); word-break: break-word;
}
#tooltip b { color: #4e79a7; }
</style>
</head>
<body>
<div id="toolbar">
  <span id="toolbar-title">🕸 知识图谱</span>
  <input id="search" type="text" placeholder="搜索节点…" autocomplete="off"/>
  <span id="stats"></span>
</div>
<div id="container">
  <svg id="svg"></svg>
  <div id="tooltip"></div>
</div>
<script src="https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js"></script>
<script>
(function () {
  var GRAPH = ${graphJson};
  var NODE_FILL = "#4e79a7";
  var NODE_STROKE = "#2c5a87";

  var container = document.getElementById("container");
  var tooltip   = document.getElementById("tooltip");
  var statsEl   = document.getElementById("stats");
  statsEl.textContent = GRAPH.nodes.length + " 节点  " + GRAPH.edges.length + " 边";

  var svg = d3.select("#svg");
  var width  = container.clientWidth;
  var height = container.clientHeight;

  var zoom = d3.zoom().scaleExtent([0.04, 12]).on("zoom", function(e) {
    g.attr("transform", e.transform);
  });
  svg.call(zoom);
  var g = svg.append("g");

  /* Compute node degrees */
  var degMap = {};
  GRAPH.edges.forEach(function(e) {
    degMap[e.source] = (degMap[e.source] || 0) + 1;
    degMap[e.target] = (degMap[e.target] || 0) + 1;
  });
  GRAPH.nodes.forEach(function(n) { n._deg = degMap[n.id] || 0; });
  var maxDeg = d3.max(GRAPH.nodes, function(d) { return d._deg; }) || 1;
  var sizeScale = d3.scaleSqrt().domain([0, maxDeg]).range([4, 20]);

  var simulation = d3.forceSimulation(GRAPH.nodes)
    .force("link", d3.forceLink(GRAPH.edges).id(function(d) { return d.id; }).distance(75))
    .force("charge", d3.forceManyBody().strength(-190))
    .force("center", d3.forceCenter(width / 2, height / 2))
    .force("collision", d3.forceCollide(function(d) { return sizeScale(d._deg) + 2; }));

  var link = g.append("g")
    .selectAll("line")
    .data(GRAPH.edges)
    .enter().append("line")
    .attr("class", "link")
    .attr("stroke-width", 1.2)
    .on("mouseover", function(e, d) {
      var src = d.source.id || d.source;
      var tgt = d.target.id || d.target;
      var kw  = d.keywords ? ("<br><b>关键词:</b> " + esc(d.keywords)) : "";
      showTip(e, "<b>关系</b><br>" + esc(src) + " → " + esc(tgt) + kw);
    })
    .on("mouseout", hideTip);

  var node = g.append("g")
    .selectAll("g")
    .data(GRAPH.nodes)
    .enter().append("g")
    .attr("class", "node")
    .call(
      d3.drag()
        .on("start", function(e, d) { if (!e.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
        .on("drag",  function(e, d) { d.fx = e.x; d.fy = e.y; })
        .on("end",   function(e, d) { if (!e.active) simulation.alphaTarget(0); })
    );

  node.append("circle")
    .attr("r", function(d) { return sizeScale(d._deg); })
    .attr("fill", NODE_FILL)
    .attr("stroke", NODE_STROKE)
    .on("mouseover", function(e, d) {
      var desc = d.description ? ("<br><span style='color:#6b7280'>" + esc(d.description.slice(0, 130)) + (d.description.length > 130 ? "…" : "") + "</span>") : "";
      showTip(e, "<b>" + esc(d.id) + "</b>" + desc);
    })
    .on("mouseout", hideTip)
    .on("dblclick", function(e, d) {
      e.stopPropagation();
      if (d.fx !== undefined) { delete d.fx; delete d.fy; } else { d.fx = d.x; d.fy = d.y; }
    });

  node.append("text")
    .text(function(d) { return d.id; })
    .attr("dx", function(d) { return sizeScale(d._deg) + 4; })
    .attr("dy", "0.35em");

  simulation.on("tick", function() {
    link
      .attr("x1", function(d) { return d.source.x; })
      .attr("y1", function(d) { return d.source.y; })
      .attr("x2", function(d) { return d.target.x; })
      .attr("y2", function(d) { return d.target.y; });
    node.attr("transform", function(d) { return "translate(" + d.x + "," + d.y + ")"; });
  });

  /* Search */
  document.getElementById("search").addEventListener("input", function() {
    var q = this.value.toLowerCase().trim();
    node.select("circle").attr("opacity", function(d) { return !q || d.id.toLowerCase().includes(q) ? 1 : 0.12; });
    node.select("text").attr("opacity",   function(d) { return !q || d.id.toLowerCase().includes(q) ? 1 : 0.12; });
    link.attr("opacity", q ? 0.15 : 0.7);
  });

  /* Tooltip helpers */
  function showTip(e, html) { tooltip.innerHTML = html; tooltip.style.display = "block"; moveTip(e); }
  function hideTip()        { tooltip.style.display = "none"; }
  function moveTip(e)       { tooltip.style.left = (e.offsetX + 16) + "px"; tooltip.style.top = (e.offsetY - 8) + "px"; }
  function esc(s)           { return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }
  svg.on("mousemove", moveTip);

  /* Resize */
  new ResizeObserver(function() {
    width  = container.clientWidth;
    height = container.clientHeight;
    simulation.force("center", d3.forceCenter(width / 2, height / 2)).alpha(0.1).restart();
  }).observe(container);
})();
</script>
</body>
</html>`;
}

export async function GET(_req: NextRequest, ctx: Ctx) {
  try {
    const auth = requireAuthenticated(await getAuthFromRequest(_req));
    const { courseId } = await ctx.params;

    // Verify course access (throws 403/404 for unknown/forbidden courses)
    const course = await getCourseIfMember(auth.sub, auth.role as UserRole, courseId);

    // workspace = "course_<lowercase uuid>" (matches LightRAG course_id_to_workspace)
    const workspace = `course_${courseId.toLowerCase()}`;

    // Resolve table names — configurable via env, validated by regex before interpolation
    const entityTable = validateTableName(
      process.env.LIGHTRAG_ENTITY_TABLE ?? "lightrag_vdb_entity_bge_m3_1024d",
    );
    const relationTable = validateTableName(
      process.env.LIGHTRAG_RELATION_TABLE ?? "lightrag_vdb_relation_bge_m3_1024d",
    );

    // Fetch entities for this course workspace
    const entityRows = await prisma.$queryRawUnsafe<
      { entity_name: string; content: string }[]
    >(
      `SELECT entity_name, content FROM "${entityTable}" WHERE workspace = $1 LIMIT 500`,
      workspace,
    );

    if (entityRows.length === 0) {
      return new NextResponse(null, { status: 204 });
    }

    // Build node ID set for edge filtering (only keep edges with both ends in the node set)
    const nodeSet = new Set<string>(entityRows.map((r) => r.entity_name));

    // Fetch relations
    const relationRows = await prisma.$queryRawUnsafe<
      { source_id: string; target_id: string; content: string }[]
    >(
      `SELECT source_id, target_id, content FROM "${relationTable}" WHERE workspace = $1 LIMIT 2000`,
      workspace,
    );

    // Build nodes: extract description from content (format: "{name}\n{description}<SEP>...")
    const nodes = entityRows.map((r) => {
      const newlineIdx = r.content.indexOf("\n");
      const rawDesc = newlineIdx >= 0 ? r.content.slice(newlineIdx + 1) : "";
      const description = rawDesc.split("<SEP>")[0].trim();
      return { id: r.entity_name, description };
    });

    // Build edges: filter to known nodes, extract keywords from content
    // Relation content format: "{keywords}\t{src}\n{tgt}\n{description}"
    const edges = relationRows
      .filter((r) => nodeSet.has(r.source_id) && nodeSet.has(r.target_id))
      .map((r) => {
        const tabIdx = r.content.indexOf("\t");
        const keywords = tabIdx >= 0 ? r.content.slice(0, tabIdx).trim() : "";
        return { source: r.source_id, target: r.target_id, keywords };
      });

    const html = buildGraphHtml(course.name, nodes, edges);

    return new NextResponse(html, {
      status: 200,
      headers: {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
      },
    });
  } catch (e) {
    if (e instanceof ApiError) {
      return NextResponse.json(e.toBody(), { status: e.status });
    }
    console.error("[knowledge-graph-html] Unexpected error:", e);
    return NextResponse.json(
      {
        error: {
          code: "INTERNAL_ERROR",
          message: "Internal server error",
          details: {},
        },
      },
      { status: 500 },
    );
  }
}
