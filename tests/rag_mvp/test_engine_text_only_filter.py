"""Unit tests for text-only filtering before course ingest."""

from __future__ import annotations

from rag_mvp.engine import _filter_text_only_content


def test_filter_text_only_content_keeps_text_and_counts_skipped() -> None:
    content = [
        {"type": "text", "text": "a"},
        {"type": "image", "img_path": "x.png"},
        {"type": "list", "text": "item"},
        {"type": "text", "text": "b"},
    ]

    kept, skipped = _filter_text_only_content(content)

    assert len(kept) == 2
    assert all(it.get("type") == "text" for it in kept)
    assert skipped == {"image": 1, "list": 1}
