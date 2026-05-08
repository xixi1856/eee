"""Mindmap generation from MinerU-parsed Markdown files.

Two modes:
  structure  – parse MD headings into a tree (instant, no LLM)
  llm        – chunk the MD, use LLM to extract concept trees, then merge
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from loguru import logger

from .config import settings

# ---------------------------------------------------------------------------
# Output directory
# ---------------------------------------------------------------------------
MINDMAP_DIR = Path("mindmap_storage")


# ---------------------------------------------------------------------------
# MD file discovery
# ---------------------------------------------------------------------------

def find_md_files(source: str | Path) -> list[Path]:
    """Return MinerU-parsed .md files matching *source*.

    source can be:
      - A stem name like "运输层"  → searches output/parsed/{stem}/**/*.md
      - A .md file path directly
      - A directory            → all *.md under output/parsed/{dir}/**
    """
    source = Path(source)

    # Direct .md file
    if source.suffix.lower() == ".md" and source.exists():
        return [source]

    # Stem-based search under output/parsed/
    candidates: list[Path] = []
    base = settings.output_dir  # e.g. output/parsed

    if source.is_dir():
        search_root = source
    else:
        # Try matching any sub-directory whose name starts with the stem
        stem = source.name
        matches = [p for p in base.iterdir() if p.is_dir() and p.name.startswith(stem)]
        if not matches:
            raise FileNotFoundError(
                f"No parsed directory found for '{stem}' under {base}.\n"
                "Run 'rag ingest' or 'rag parse' first."
            )
        search_root = matches[0] if len(matches) == 1 else base / stem

    for md in search_root.rglob("*.md"):
        # Skip tiny stub files
        if md.stat().st_size > 500:
            candidates.append(md)
    if not candidates:
        raise FileNotFoundError(f"No .md files found under {search_root}.")
    return candidates


# ---------------------------------------------------------------------------
# Heading depth inference
# ---------------------------------------------------------------------------

_RE_CHAPTER = re.compile(r"^第[一二三四五六七八九十百\d]+[章节篇]")
_RE_L2 = re.compile(r"^\d+\.\d+\b")
_RE_L3 = re.compile(r"^\d+\.\d+\.\d+\b")
_NOISE_HEADERS = {"目", "录", "目录", "参考文献", "References"}


def _infer_depth(text: str) -> int:
    t = text.strip()
    if _RE_CHAPTER.match(t):
        return 1
    if _RE_L3.match(t):
        return 3
    if _RE_L2.match(t):
        return 2
    return 3


# ---------------------------------------------------------------------------
# Mode 1: structure – parse MD headings
# ---------------------------------------------------------------------------

def _parse_md_tree(md_path: Path) -> dict[str, Any]:
    """Build a hierarchy dict from ## headings in the markdown file."""
    text = md_path.read_text(encoding="utf-8")
    root_name = md_path.stem
    root: dict[str, Any] = {"name": root_name, "children": []}

    # Split on ## headings
    sections = re.split(r"\n(?=## )", text)
    stack: list[tuple[int, dict]] = [(0, root)]  # (depth, node)

    seen_at_depth: dict[int, str] = {}  # dedup consecutive repeated headings

    for section in sections:
        lines = section.strip().split("\n")
        if not lines:
            continue

        first = lines[0]
        if first.startswith("## "):
            heading = first[3:].strip()
            if not heading or heading in _NOISE_HEADERS:
                continue

            depth = _infer_depth(heading)

            # Deduplicate: skip if same heading appeared at same depth consecutively
            if seen_at_depth.get(depth) == heading:
                continue
            seen_at_depth[depth] = heading

            # Collect bullet lines from body (non-empty, not sub-headings)
            bullets: list[str] = []
            for line in lines[1:]:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # Keep meaningful lines, skip decorative/noise
                if len(line) < 3 or re.match(r"^[-=*]+$", line):
                    continue
                bullets.append(line)
                if len(bullets) >= 6:
                    break

            node: dict[str, Any] = {"name": heading}
            if bullets:
                node["children"] = [{"name": b} for b in bullets]
            else:
                node["children"] = []

            # Find parent: pop stack until we find a shallower depth
            while len(stack) > 1 and stack[-1][0] >= depth:
                stack.pop()
            stack[-1][1].setdefault("children", []).append(node)
            stack.append((depth, node))

    return root


# ---------------------------------------------------------------------------
# Phase 1: Semantic Aggregation (Refining) – Qwen-Long long-context pass
# ---------------------------------------------------------------------------

_REFINE_PROMPT = """\
你是一位计算机网络技术文档整理专家。请仔细阅读以下文档，将其整理成一份结构化的技术学习笔记。

整理要求：
1. **保留所有核心技术概念**：协议名称、机制、算法、数据结构、字段含义等
2. **去除所有比喻和类比**：如"张三李四"、"快递员"、"信件"等生活化比喻，只保留技术本身
3. **保留解释性内容**：概念的定义、作用、工作原理、优缺点等
4. **使用标准 Markdown 标题**：
   - `##` 用于一级主题（如"多路复用与分用"）
   - `###` 用于二级子主题（如"TCP 多路复用"）
   - `####` 用于三级细节主题（可选）
5. **每个标题下用短语列出要点**（格式：`- 要点`），每条不超过 25 字
6. **每个标题下至多 8 个要点**
7. **只输出 Markdown 内容，不要有任何说明文字或前言**

文档内容：
{text}"""


def _parse_refined_text(text: str, stem: str) -> dict[str, Any]:
    """Parse a structured markdown outline (##/###/####) produced by LLM refining.

    Heading level maps directly: ## → depth-1, ### → depth-2, #### → depth-3.
    """
    root: dict[str, Any] = {"name": stem, "children": []}
    stack: list[tuple[int, dict]] = [(0, root)]
    seen_at_level: dict[int, str] = {}
    current_node: dict[str, Any] | None = None
    body_lines: list[str] = []

    def flush_bullets() -> None:
        nonlocal body_lines
        if current_node is not None and body_lines:
            bullets: list[str] = []
            for raw in body_lines:
                clean = re.sub(r"^[-*•·]\s*", "", raw.strip())
                if clean and len(clean) >= 3 and not re.match(r"^[-=*]+$", clean):
                    bullets.append(clean)
                    if len(bullets) >= 6:
                        break
            if bullets and not current_node.get("children"):
                current_node["children"] = [{"name": b} for b in bullets]
        body_lines.clear()

    for line in text.splitlines():
        m = re.match(r"^(#{2,4}) (.+)", line)
        if m:
            flush_bullets()
            level = len(m.group(1)) - 1  # ## → 1, ### → 2, #### → 3
            heading = m.group(2).strip()
            if not heading or heading in _NOISE_HEADERS:
                current_node = None
                continue
            if seen_at_level.get(level) == heading:
                current_node = None
                continue
            seen_at_level[level] = heading
            node: dict[str, Any] = {"name": heading, "children": []}
            while len(stack) > 1 and stack[-1][0] >= level:
                stack.pop()
            stack[-1][1].setdefault("children", []).append(node)
            stack.append((level, node))
            current_node = node
        elif line.strip() and current_node is not None:
            body_lines.append(line.strip())

    flush_bullets()
    return root


async def _refine_with_llm(text: str) -> str:
    """Send the full document text to Qwen-Long and return a structured outline."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
    )
    prompt = _REFINE_PROMPT.replace("{text}", text)
    logger.info(f"[refine] Sending {len(text):,} chars to {settings.refine_model} …")
    response = await client.chat.completions.create(
        model=settings.refine_model,
        messages=[
            {"role": "system", "content": "You are a helpful technical notes organizer."},
            {"role": "user", "content": prompt},
        ],
        max_tokens=8192,
        temperature=0.1,
    )
    refined = response.choices[0].message.content or ""
    logger.debug(f"[refine] raw response:\n{refined}")
    logger.info(f"[refine] Received {len(refined):,} chars from model")
    return refined


def build_structure_mindmap(source: str | Path, refine: bool = False) -> list[Path]:
    """Generate structure-mode mindmaps for all MD files matching *source*.

    Args:
        source: file stem, .md path, or directory.
        refine: if True, run Phase 1 (Semantic Aggregation via Qwen-Long) before
                parsing headings.  The entire document is sent to the model in one
                call – no chunking.

    Returns:
        List of generated HTML paths.
    """
    md_files = find_md_files(source)
    MINDMAP_DIR.mkdir(parents=True, exist_ok=True)
    results: list[Path] = []
    for md in md_files:
        if refine:
            logger.info(f"Phase 1 – Semantic Aggregation: {md.name} → {settings.refine_model}")
            raw_text = md.read_text(encoding="utf-8")
            refined_text = asyncio.run(_refine_with_llm(raw_text))
            logger.info("Phase 2 – Parsing refined outline …")
            tree = _parse_refined_text(refined_text, md.stem)
        else:
            tree = _parse_md_tree(md)
        out = MINDMAP_DIR / f"mindmap_{md.stem}.html"
        _write_html(tree, md.stem, out)
        results.append(out)
        logger.success(f"Mindmap saved: {out}")
    return results


# ---------------------------------------------------------------------------
# Mode 2: llm – chunk → local trees → merge
# ---------------------------------------------------------------------------

_CHUNK_TOKEN_LIMIT = 1500  # approx tokens per chunk (by chars * 0.6 heuristic)


def _split_md_into_chunks(md_path: Path, max_chars: int = 2500) -> list[str]:
    """Split MD by headings into chunks of roughly max_chars characters."""
    text = md_path.read_text(encoding="utf-8")
    sections = re.split(r"\n(?=## )", text)
    chunks: list[str] = []
    current = ""
    for sec in sections:
        if len(current) + len(sec) > max_chars and current:
            chunks.append(current.strip())
            current = sec
        else:
            current += "\n" + sec
    if current.strip():
        chunks.append(current.strip())
    return [c for c in chunks if len(c) > 50]


_EXTRACT_PROMPT = """\
你是一个思维导图构建专家。请阅读以下文档片段，提取其中的核心概念及层级关系，输出严格的 JSON 树结构。

要求：
- 尽量提取文档中的所有核心概念，不要丢信息
- 深度最多 5 层
- 每个节点最多 8 个子节点
- name 字段为概念名称，尽量简洁但完整（允许 20 字以内）
- 保持层级逻辑和父子关系
- 只输出 JSON，不要有任何解释文字
- 格式示例: {"name": "主题", "children": [{"name": "...", "children": [...]}]}

文档片段：
{chunk}
"""

_MERGE_PROMPT = """\
你是一个思维导图专家。请将以下多棵思维导图 JSON 树合并为一棵完整的树。

要求：
- 合并重复或高度相似的节点
- 保留所有核心概念，尽量完整
- 保持清晰层级关系
- 深度最多 5 层，每个节点最多 10 个子节点
- 只输出合并后的单个 JSON 树，不要有任何解释文字
- 格式示例: {"name": "根节点", "children": [...]}

待合并的树列表（JSON 数组）：
{trees_json}
"""


def _extract_json(text: str) -> dict:
    """Extract first JSON object from LLM response."""
    text = text.strip()
    # Strip markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    # Find first { ... }
    start = text.find("{")
    if start == -1:
        raise ValueError(f"No JSON found in LLM response: {text[:200]}")
    # Find matching closing brace
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError("Malformed JSON in LLM response")


async def _llm_call(prompt: str) -> str:
    from .llm import llm_model_func
    return await llm_model_func(prompt, system_prompt="You are a helpful assistant.")


async def _extract_chunk_tree(chunk: str, stem: str) -> dict:
    prompt = _EXTRACT_PROMPT.replace("{chunk}", chunk)
    resp: str | None = None
    try:
        resp = await _llm_call(prompt)
        logger.debug(f"[chunk-extract] raw response:\n{resp}")
        tree = _extract_json(resp)
        return tree
    except Exception as exc:
        logger.warning(f"Chunk extraction failed ({exc}), using fallback node")
        logger.debug(f"[chunk-extract] failed response was:\n{resp if resp is not None else '(no response)'}")
        return {"name": stem, "children": []}


async def _merge_trees(trees: list[dict], root_name: str) -> dict:
    if len(trees) == 1:
        return trees[0]
    trees_json = json.dumps(trees, ensure_ascii=False)
    prompt = _MERGE_PROMPT.replace("{trees_json}", trees_json)
    resp: str | None = None
    try:
        resp = await _llm_call(prompt)
        logger.debug(f"[merge] raw response:\n{resp}")
        merged = _extract_json(resp)
        if "name" not in merged:
            merged["name"] = root_name
        return merged
    except Exception as exc:
        logger.warning(f"Merge failed ({exc}), stitching manually")
        logger.debug(f"[merge] failed response was:\n{resp if resp is not None else '(no response)'}")
        return {"name": root_name, "children": trees}


async def _build_llm_tree(md_path: Path, max_chars: int) -> dict:
    stem = md_path.stem
    chunks = _split_md_into_chunks(md_path, max_chars)
    logger.info(f"{stem}: {len(chunks)} chunks to process")

    # Chunk Loop
    local_trees: list[dict] = []
    for i, chunk in enumerate(chunks, 1):
        logger.info(f"  chunk {i}/{len(chunks)} …")
        tree = await _extract_chunk_tree(chunk, stem)
        local_trees.append(tree)

    # Merge Loop: batch-merge in groups of 3 until single tree
    batch = 3
    while len(local_trees) > 1:
        logger.info(f"  merging {len(local_trees)} trees …")
        next_round: list[dict] = []
        for i in range(0, len(local_trees), batch):
            group = local_trees[i : i + batch]
            merged = await _merge_trees(group, stem)
            next_round.append(merged)
        local_trees = next_round

    final = local_trees[0] if local_trees else {"name": stem, "children": []}
    if not final.get("name"):
        final["name"] = stem
    return final


def build_llm_mindmap(
    source: str | Path,
    max_chars: int = 2500,
) -> list[Path]:
    """Generate LLM-mode mindmaps. Returns list of HTML paths."""
    md_files = find_md_files(source)
    MINDMAP_DIR.mkdir(parents=True, exist_ok=True)
    results: list[Path] = []
    for md in md_files:
        logger.info(f"Building LLM mindmap for: {md.name}")
        tree = asyncio.run(_build_llm_tree(md, max_chars))
        out = MINDMAP_DIR / f"mindmap_{md.stem}.html"
        _write_html(tree, md.stem, out)
        results.append(out)
        logger.success(f"Mindmap saved: {out}")
    return results


# ---------------------------------------------------------------------------
# HTML rendering (shared by both modes)
# ---------------------------------------------------------------------------

_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"/>
<title>TITLE_PLACEHOLDER 思维导图</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
       background: #0d1117; color: #e6edf3; height: 100vh; display: flex; flex-direction: column; }
#toolbar { display: flex; align-items: center; gap: 14px; padding: 8px 18px;
           background: #161b22; border-bottom: 1px solid #30363d; flex-shrink: 0; }
#toolbar h1 { font-size: 15px; color: #58a6ff; margin-right: auto; }
button { background: #21262d; border: 1px solid #30363d; color: #e6edf3;
         border-radius: 5px; padding: 4px 12px; cursor: pointer; font-size: 12px; }
button:hover { background: #30363d; }
#hint { font-size: 11px; color: #8b949e; }
#svg-wrap { flex: 1; overflow: hidden; }
svg { width: 100%; height: 100%; }
.node circle { cursor: pointer; stroke-width: 1.5; transition: r .15s; }
.node circle:hover { filter: brightness(1.3); }
.node text { font-size: 12px; fill: #e6edf3; pointer-events: none; }
.link { fill: none; stroke: #30363d; stroke-width: 1.5px; }
.node--collapsed circle { stroke-dasharray: 3,2; }
</style>
</head>
<body>
<div id="toolbar">
  <h1>🧠 TITLE_PLACEHOLDER</h1>
  <button id="btn-expand">全部展开</button>
  <button id="btn-collapse">全部折叠</button>
  <button id="btn-fit">适应窗口</button>
  <span id="hint">点击节点折叠/展开 · 滚轮缩放 · 拖拽平移</span>
</div>
<div id="svg-wrap"><svg id="svg"></svg></div>
<script src="https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js"></script>
<script>
const DATA = TREE_JSON_PLACEHOLDER;
const DEPTH_COLOURS = ["#58a6ff","#3fb950","#d29922","#f85149","#bc8cff","#39d353"];

const svgEl = document.getElementById("svg");
const wrap  = document.getElementById("svg-wrap");
let W = wrap.clientWidth, H = wrap.clientHeight;

const svg = d3.select(svgEl);
const g   = svg.append("g");
const zoom = d3.zoom().scaleExtent([0.05, 4]).on("zoom", e => g.attr("transform", e.transform));
svg.call(zoom);

const treeLayout = d3.tree().nodeSize([28, 260]);

// --- build hierarchy -------------------------------------------------------
function buildHierarchy(data) {
  return d3.hierarchy(data, d => (d._collapsed ? null : d.children));
}

// store original children
function initCollapse(node) {
  node._children = node.children ? [...node.children] : [];
  node._collapsed = node.depth > 1;  // collapse beyond depth 1 by default
  if (node.children) node.children.forEach(initCollapse);
}

const root0 = d3.hierarchy(DATA);
root0.each(n => { n._children = n.data.children || []; n._collapsed = n.depth > 1; });

function getChildren(d) { return d._collapsed ? null : (d._children.length ? d._children : null); }

let root = d3.hierarchy(DATA, d => getChildren({data: d, _children: d.children||[], _collapsed: false}));

// ---- stateful tree --------------------------------------------------------
let nodeData = [];

function update() {
  // Re-build hierarchy respecting _collapsed state stored in nodeData map
  function childrenFn(d) {
    const nd = nodeMap.get(d);
    if (nd && nd._collapsed) return null;
    return d.children && d.children.length ? d.children : null;
  }
  root = d3.hierarchy(DATA, childrenFn);
  nodeMap = new Map();
  root.each(n => nodeMap.set(n.data, n));
  // restore collapse state
  stateMap.forEach((collapsed, key) => {
    const n = nodeMap.get(key);
    if (n) n._collapsed = collapsed;
  });

  treeLayout(root);

  const nodes = root.descendants();
  const links = root.links();

  // links
  const link = g.selectAll(".link").data(links, d => d.target.data.__id);
  link.enter().append("path").attr("class","link")
    .merge(link)
    .attr("d", d3.linkHorizontal().x(d => d.y).y(d => d.x));
  link.exit().remove();

  // nodes
  const node = g.selectAll(".node").data(nodes, d => d.data.__id);
  const nodeEnter = node.enter().append("g").attr("class", "node")
    .attr("transform", d => `translate(${d.y},${d.x})`)
    .on("click", (e, d) => {
      if (!d._children || !d._children.length) return;
      d._collapsed = !d._collapsed;
      stateMap.set(d.data, d._collapsed);
      d3.select(e.currentTarget).classed("node--collapsed", d._collapsed);
      update();
    });
  nodeEnter.append("circle")
    .attr("r", d => d.depth === 0 ? 10 : d._children && d._children.length ? 6 : 4)
    .attr("fill", d => DEPTH_COLOURS[Math.min(d.depth, DEPTH_COLOURS.length-1)])
    .attr("stroke", d => d3.color(DEPTH_COLOURS[Math.min(d.depth, DEPTH_COLOURS.length-1)]).darker(1));
  nodeEnter.append("text")
    .attr("dy","0.32em")
    .attr("x", d => (d._children && d._children.length ? -10 : 10))
    .attr("text-anchor", d => (d._children && d._children.length ? "end" : "start"))
    .text(d => d.data.name);

  node.merge(nodeEnter)
    .transition().duration(300)
    .attr("transform", d => `translate(${d.y},${d.x})`);
  node.exit().remove();
}

// assign stable ids
let _uid = 0;
function assignIds(d) { d.__id = _uid++; if (d.children) d.children.forEach(assignIds); }
assignIds(DATA);

let nodeMap = new Map();
let stateMap = new Map();
// initialise collapse state
function initState(d, depth) {
  stateMap.set(d, depth > 1);
  if (d.children) d.children.forEach(c => initState(c, depth+1));
}
initState(DATA, 0);

update();
fit();

function fit() {
  W = wrap.clientWidth; H = wrap.clientHeight;
  const b = g.node().getBBox();
  if (!b.width) return;
  const pad = 40;
  const scale = Math.min((W-pad*2)/b.width, (H-pad*2)/b.height, 1.5);
  const tx = W/2 - scale*(b.x + b.width/2);
  const ty = H/2 - scale*(b.y + b.height/2);
  svg.transition().duration(500).call(zoom.transform, d3.zoomIdentity.translate(tx,ty).scale(scale));
}

document.getElementById("btn-fit").addEventListener("click", fit);
document.getElementById("btn-expand").addEventListener("click", () => {
  stateMap.forEach((_, k) => stateMap.set(k, false)); update(); fit();
});
document.getElementById("btn-collapse").addEventListener("click", () => {
  stateMap.forEach((_, k) => stateMap.set(k, true));
  stateMap.set(DATA, false);  // keep root expanded
  update(); fit();
});
new ResizeObserver(fit).observe(wrap);
</script>
</body>
</html>
"""


def _write_html(tree: dict[str, Any], title: str, out_path: Path) -> None:
    html = _HTML.replace("TITLE_PLACEHOLDER", title).replace(
        "TREE_JSON_PLACEHOLDER", json.dumps(tree, ensure_ascii=False)
    )
    out_path.write_text(html, encoding="utf-8")
