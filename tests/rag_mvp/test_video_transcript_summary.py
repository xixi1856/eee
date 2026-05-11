"""Unit tests for video transcript parsing, chunking, and JSON helpers (no LLM)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rag_mvp.video_transcript_summary import (
    chunk_transcript_by_duration,
    merge_and_sort_segments,
    parse_hhmmss,
    parse_json_segment_array,
    parse_timestamped_transcript,
    seconds_to_hhmmss,
    strip_json_fence,
    write_structured_summary_files,
)


def test_seconds_to_hhmmss() -> None:
    assert seconds_to_hhmmss(0) == "00:00:00"
    assert seconds_to_hhmmss(59) == "00:00:59"
    assert seconds_to_hhmmss(60) == "00:01:00"
    assert seconds_to_hhmmss(3661) == "01:01:01"


def test_parse_hhmmss() -> None:
    assert parse_hhmmss("00:05:00") == 300
    assert parse_hhmmss("01:00:00") == 3600
    with pytest.raises(ValueError):
        parse_hhmmss("00:00:60")


def test_parse_timestamped_transcript() -> None:
    text = """
[1.0s] hello
[0.5s] out of order
[10.0s] world
"""
    rows = parse_timestamped_transcript(text)
    assert rows == [(0.5, "out of order"), (1.0, "hello"), (10.0, "world")]


def test_chunk_transcript_by_duration() -> None:
    # Span from anchor crosses target_seconds → flush before each new line that exceeds it.
    lines = [(0.0, "a"), (100.0, "b"), (250.0, "c"), (400.0, "d")]
    chunks = chunk_transcript_by_duration(lines, target_seconds=200, max_seconds=500)
    assert len(chunks) == 2
    assert chunks[0] == [(0.0, "a"), (100.0, "b")]
    assert chunks[1] == [(250.0, "c"), (400.0, "d")]


def test_chunk_forces_max_split() -> None:
    lines = [(0.0, "a"), (700.0, "b")]
    chunks = chunk_transcript_by_duration(lines, target_seconds=300, max_seconds=600)
    assert len(chunks) == 2
    assert chunks[0] == [(0.0, "a")]
    assert chunks[1] == [(700.0, "b")]


def test_strip_json_fence() -> None:
    raw = "```json\n[{\"a\":1}]\n```"
    assert strip_json_fence(raw).strip() == '[{"a":1}]'


def test_parse_json_segment_array() -> None:
    s = json.dumps(
        [
            {"start_time": "00:00:00", "end_time": "00:01:00", "summary": "one"},
            {"start_time": "00:01:01", "end_time": "00:02:00", "summary": "two"},
        ],
        ensure_ascii=False,
    )
    arr = parse_json_segment_array(s)
    assert len(arr) == 2
    assert arr[0]["summary"] == "one"


def test_merge_and_sort_segments() -> None:
    merged = merge_and_sort_segments(
        [
            [{"start_time": "00:10:00", "end_time": "00:11:00", "summary": "late"}],
            [{"start_time": "00:00:00", "end_time": "00:05:00", "summary": "early"}],
        ]
    )
    assert [m["summary"] for m in merged] == ["early", "late"]


def test_merge_drops_empty_summary() -> None:
    merged = merge_and_sort_segments(
        [[{"start_time": "00:00:00", "end_time": "00:01:00", "summary": ""}]]
    )
    assert merged == []


def test_write_structured_summary_files(tmp_path: Path) -> None:
    segs = [{"start_time": "00:00:00", "end_time": "00:01:00", "summary": "S"}]
    jp, mp = write_structured_summary_files("demo", segs, tmp_path)
    assert jp.exists() and mp.exists()
    data = json.loads(jp.read_text(encoding="utf-8"))
    assert data == segs
    assert "00:00:00" in mp.read_text(encoding="utf-8")
