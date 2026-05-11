"""Parse timestamped Whisper transcripts, chunk by duration, LLM → JSON + Markdown for RAG ingest."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from loguru import logger

from .config import Settings, settings

_LINE_RE = re.compile(r"^\s*\[(\d+(?:\.\d+)?)s\]\s*(.*)\s*$")

DEFAULT_VIDEO_SUMMARY_SYSTEM_PROMPT = """你是一个视频内容摘要生成专家。目标是从完整转录文本生成结构化 summary，用于知识库 ingest 和后续索引。

要求：
1. 输入是一段长视频的转录文本片段，已标注时间戳（形如 [秒数s]）。
2. 将本片段内的内容按时间段分段，每段约 3~10 分钟；若本片段不足 3 分钟，可输出单段。
3. 每段输出应包含：
   - start_time: 段开始时间（HH:MM:SS）
   - end_time: 段结束时间（HH:MM:SS）
   - summary: 对该段内容的详细总结。要求：
     - 详细描述讲解主题
     - 记录出现的重要概念、名词和术语
     - 说明例子、演示或应用场景
     - 提及逻辑推导或步骤
     - 保留视频讲解的重点和关键细节
4. 避免逐字转录原文，尽量减少噪音，但要保证 summary 足够详细，可以让不看原视频的人理解主要内容。
5. 只输出一个 JSON 数组，不要输出任何其他文字、不要 Markdown 代码围栏。每个元素形如：
   {"start_time":"00:00:00","end_time":"00:05:00","summary":"..."}
6. 各段的 start_time / end_time 必须落在本片段给定的时间范围内（秒边界已说明）。"""


def seconds_to_hhmmss(total_seconds: float) -> str:
    """Floor to whole seconds for stable labels."""
    s = max(0, int(total_seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"


def parse_hhmmss(label: str) -> int:
    """Parse HH:MM:SS to total seconds."""
    parts = label.strip().split(":")
    if len(parts) != 3:
        raise ValueError(f"Invalid time label: {label!r}")
    h, m, s = (int(parts[0]), int(parts[1]), int(parts[2]))
    if m >= 60 or s >= 60:
        raise ValueError(f"Invalid time label: {label!r}")
    return h * 3600 + m * 60 + s


def parse_timestamped_transcript(text: str) -> list[tuple[float, str]]:
    """Return ordered (start_seconds, line_text_without_bracket) from Whisper export."""
    lines: list[tuple[float, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        t = float(m.group(1))
        body = (m.group(2) or "").strip()
        lines.append((t, body))
    lines.sort(key=lambda x: x[0])
    return lines


def chunk_transcript_by_duration(
    lines: list[tuple[float, str]],
    *,
    target_seconds: float,
    max_seconds: float,
) -> list[list[tuple[float, str]]]:
    """Split lines into chunks where span (last_ts - anchor) respects target/max."""
    if not lines:
        return []
    if max_seconds < target_seconds:
        max_seconds = target_seconds

    chunks: list[list[tuple[float, str]]] = []
    current: list[tuple[float, str]] = []
    anchor: float | None = None

    for t, body in lines:
        if anchor is None:
            anchor = t

        assert anchor is not None
        span = t - anchor

        if current and span > max_seconds:
            chunks.append(current)
            current = [(t, body)]
            anchor = t
            continue

        if current and span >= target_seconds:
            chunks.append(current)
            current = [(t, body)]
            anchor = t
            continue

        current.append((t, body))

    if current:
        chunks.append(current)
    return chunks


def _format_chunk_for_prompt(rows: list[tuple[float, str]]) -> str:
    return "\n".join(f"[{t:.1f}s] {txt}" if txt else f"[{t:.1f}s]" for t, txt in rows)


def strip_json_fence(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```\s*$", "", s)
    return s.strip()


def parse_json_segment_array(raw: str) -> list[dict[str, Any]]:
    """Parse model output as JSON array of segment dicts."""
    s = strip_json_fence(raw)
    data = json.loads(s)
    if not isinstance(data, list):
        raise ValueError("JSON root must be an array")
    out: list[dict[str, Any]] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"Item {i} must be an object")
        for k in ("start_time", "end_time", "summary"):
            if k not in item:
                raise ValueError(f"Item {i} missing key {k!r}")
        out.append(
            {
                "start_time": str(item["start_time"]).strip(),
                "end_time": str(item["end_time"]).strip(),
                "summary": str(item["summary"]).strip(),
            }
        )
    return out


def merge_and_sort_segments(chunks: list[list[dict[str, Any]]]) -> list[dict[str, str]]:
    """Flatten, sort by start_time, drop empty summaries."""
    flat: list[dict[str, str]] = []
    for part in chunks:
        for seg in part:
            st = seg.get("start_time", "")
            et = seg.get("end_time", "")
            sm = seg.get("summary", "")
            if not sm:
                continue
            flat.append({"start_time": st, "end_time": et, "summary": sm})

    def key_fn(seg: dict[str, str]) -> tuple[int, int]:
        try:
            a = parse_hhmmss(seg["start_time"])
            b = parse_hhmmss(seg["end_time"])
        except ValueError:
            return (10**9, 10**9)
        return (a, b)

    flat.sort(key=key_fn)
    return flat


def segments_to_markdown(segments: list[dict[str, str]], *, title: str = "视频结构化摘要") -> str:
    parts = [f"# {title}\n"]
    for seg in segments:
        st, et, sm = seg["start_time"], seg["end_time"], seg["summary"]
        parts.append(f"## {st} – {et}\n")
        parts.append(f"**start_time:** {st}\n")
        parts.append(f"**end_time:** {et}\n\n")
        parts.append(sm + "\n\n")
    return "".join(parts).rstrip() + "\n"


async def _call_llm_chunk(
    chunk_rows: list[tuple[float, str]],
    *,
    cfg: Settings,
    system_prompt: str,
    model: str,
) -> list[dict[str, Any]]:
    from lightrag.llm.openai import openai_complete_if_cache

    if not chunk_rows:
        return []

    t_start = chunk_rows[0][0]
    t_end = chunk_rows[-1][0]
    chunk_text = _format_chunk_for_prompt(chunk_rows)
    user = (
        f"本片段在整段视频中的时间范围约为 {seconds_to_hhmmss(t_start)} 至 {seconds_to_hhmmss(t_end)} "
        f"（约 {t_start:.1f}s – {t_end:.1f}s）。请只基于下列转录生成 JSON 数组。\n\n"
        f"{chunk_text}"
    )

    last_err: Exception | None = None
    for attempt in range(2):
        try:
            raw = await openai_complete_if_cache(
                model,
                user,
                system_prompt=system_prompt,
                history_messages=[],
                api_key=cfg.llm_api_key,
                base_url=cfg.llm_base_url,
                max_tokens=cfg.llm_max_tokens,
                temperature=cfg.llm_temperature,
            )
            return parse_json_segment_array(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            last_err = exc
            logger.warning("structured summary JSON parse failed (attempt {}): {}", attempt + 1, exc)
        except Exception as exc:
            last_err = exc
            logger.warning("structured summary LLM failed (attempt {}): {}", attempt + 1, exc)
            raise
    if last_err:
        raise last_err
    return []


async def generate_structured_segments_async(
    transcript_text: str,
    *,
    cfg: Settings | None = None,
    target_seconds: float | None = None,
    max_seconds: float | None = None,
) -> list[dict[str, str]]:
    cfg = cfg or settings
    target = float(target_seconds if target_seconds is not None else cfg.video_summary_target_segment_seconds)
    max_seg = float(max_seconds if max_seconds is not None else cfg.video_summary_max_segment_seconds)
    if max_seg < target:
        max_seg = target

    system = (cfg.video_summary_system_prompt or "").strip() or DEFAULT_VIDEO_SUMMARY_SYSTEM_PROMPT
    model = (cfg.video_summary_llm_model or "").strip() or cfg.refine_model

    lines = parse_timestamped_transcript(transcript_text)
    if not lines:
        logger.warning("No timestamped lines found in transcript; structured summary skipped.")
        return []

    chunks = chunk_transcript_by_duration(lines, target_seconds=target, max_seconds=max_seg)
    results: list[list[dict[str, Any]]] = []
    for i, chunk in enumerate(chunks):
        logger.info("Structured summary LLM chunk {}/{}", i + 1, len(chunks))
        part = await _call_llm_chunk(chunk, cfg=cfg, system_prompt=system, model=model)
        results.append(part)

    merged = merge_and_sort_segments(results)
    return merged


def generate_structured_segments_sync(
    transcript_text: str,
    *,
    cfg: Settings | None = None,
    target_seconds: float | None = None,
    max_seconds: float | None = None,
) -> list[dict[str, str]]:
    return asyncio.run(
        generate_structured_segments_async(
            transcript_text,
            cfg=cfg,
            target_seconds=target_seconds,
            max_seconds=max_seconds,
        )
    )


def write_structured_summary_files(
    stem: str,
    segments: list[dict[str, str]],
    out_dir: Path,
    *,
    md_title: str | None = None,
) -> tuple[Path, Path]:
    """Write ``<stem>.summary.json`` and ``<stem>.summary.md`` under out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{stem}.summary.json"
    md_path = out_dir / f"{stem}.summary.md"
    json_path.write_text(
        json.dumps(segments, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    md = segments_to_markdown(segments, title=md_title or f"视频结构化摘要 · {stem}")
    md_path.write_text(md, encoding="utf-8")
    return json_path, md_path


def build_structured_summary_from_transcript_text(
    transcript_text: str,
    stem: str,
    out_dir: Path,
    *,
    cfg: Settings | None = None,
    target_seconds: float | None = None,
    max_seconds: float | None = None,
    md_title: str | None = None,
) -> tuple[list[dict[str, str]], Path, Path]:
    """Run LLM pipeline and write json+md; returns segments and paths."""
    segments = generate_structured_segments_sync(
        transcript_text,
        cfg=cfg,
        target_seconds=target_seconds,
        max_seconds=max_seconds,
    )
    jp, mp = write_structured_summary_files(stem, segments, out_dir, md_title=md_title)
    return segments, jp, mp
