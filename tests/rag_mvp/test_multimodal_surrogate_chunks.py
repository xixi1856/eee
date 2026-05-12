"""Tests for MinerU multimodal → surrogate text → custom KG chunks."""

from __future__ import annotations

import pytest
from types import SimpleNamespace

from lightrag.utils import TiktokenTokenizer

from rag_mvp.multimodal_surrogate_chunks import (
    _VISION_SKIPPED_DECORATIVE,
    content_item_to_surrogate_text,
    content_item_to_surrogate_text_async,
    merge_file_chunks_with_global_indices,
    multimodal_items_to_custom_chunks,
    multimodal_items_to_custom_chunks_async,
)

# 1×1 transparent PNG (minimal valid file)
_MIN_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\x0b.f"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_content_item_image_uses_caption_and_basename() -> None:
    s = content_item_to_surrogate_text(
        {
            "type": "image",
            "img_path": "/tmp/out/foo/bar.png",
            "image_caption": ["A diagram"],
            "image_footnote": ["See page 2"],
        },
    )
    assert "[Image]" in s
    assert "Caption: A diagram" in s
    assert "bar.png" in s
    assert "Note: See page 2" in s


def test_content_item_table_markdown_list_of_lists() -> None:
    s = content_item_to_surrogate_text(
        {
            "type": "table",
            "table_caption": ["Sales"],
            "table_body": [["A", "B"], [1, 2]],
        },
    )
    assert "[Table]" in s
    assert "Sales" in s
    assert "| A | B |" in s


def test_content_item_equation() -> None:
    s = content_item_to_surrogate_text(
        {"type": "equation", "latex": "E = mc^2", "text": "Energy"},
    )
    assert "[Equation]" in s
    assert "Energy" in s or "mc^2" in s


def test_content_item_code() -> None:
    s = content_item_to_surrogate_text(
        {"type": "code", "language": "python", "code": "print(1)"},
    )
    assert "[Code]" in s
    assert "python" in s
    assert "print(1)" in s


def test_content_item_list_from_text() -> None:
    s = content_item_to_surrogate_text({"type": "list", "text": "one\ntwo"})
    assert "[List]" in s


def test_multimodal_items_to_custom_chunks_order_and_file_path() -> None:
    tok = TiktokenTokenizer(model_name="gpt-4o-mini")
    lr = SimpleNamespace(
        tokenizer=tok,
        chunk_overlap_token_size=10,
        chunk_token_size=200,
    )
    mm = [
        {"type": "image", "img_path": "x.png", "image_caption": ["Fig 1"]},
        {"type": "equation", "text": "x"},
    ]
    chunks = multimodal_items_to_custom_chunks(
        lr,  # type: ignore[arg-type]
        mm,
        "mat_id_doc.pdf",
        order_base=5,
    )
    assert chunks
    assert all(c["file_path"] == "mat_id_doc.pdf" for c in chunks)
    assert chunks[0]["chunk_order_index"] == 5
    for i in range(1, len(chunks)):
        assert chunks[i]["chunk_order_index"] > chunks[i - 1]["chunk_order_index"]


def test_merge_file_chunks_renumbers_source_ids() -> None:
    per_file = [
        [
            {"content": "a", "source_id": "c0", "file_path": "f1", "chunk_order_index": 0},
        ],
        [
            {"content": "b", "source_id": "c0", "file_path": "f2", "chunk_order_index": 0},
        ],
    ]
    merged = merge_file_chunks_with_global_indices(per_file)
    assert len(merged) == 2
    assert merged[0]["source_id"] == "c0"
    assert merged[1]["source_id"] == "c1"
    assert merged[0]["chunk_order_index"] == 0
    assert merged[1]["chunk_order_index"] == 1
    assert merged[0]["content"] == "a"
    assert merged[1]["content"] == "b"


@pytest.mark.asyncio
async def test_content_item_async_image_vlm_appends_summary(monkeypatch, tmp_path) -> None:
    async def fake_summary(_b64: str, _mime: str) -> str:
        return "流程图从输入到输出。"

    monkeypatch.setattr(
        "rag_mvp.multimodal_surrogate_chunks._call_vision_image_summary",
        fake_summary,
    )
    p = tmp_path / "one.png"
    p.write_bytes(_MIN_PNG)
    item = {"type": "image", "img_path": str(p), "image_caption": ["图1"]}
    text = await content_item_to_surrogate_text_async(
        item,
        use_vlm_for_images=True,
        vlm_semaphore=None,
    )
    assert "Visual summary:" in text
    assert "流程图" in text
    assert "图1" in text


@pytest.mark.asyncio
async def test_content_item_async_image_vlm_failure_falls_back(monkeypatch, tmp_path) -> None:
    async def boom(_b64: str, _mime: str) -> str:
        raise RuntimeError("api down")

    monkeypatch.setattr(
        "rag_mvp.multimodal_surrogate_chunks._call_vision_image_summary",
        boom,
    )
    p = tmp_path / "two.png"
    p.write_bytes(_MIN_PNG)
    item = {"type": "image", "img_path": str(p), "image_caption": ["Only caption"]}
    text = await content_item_to_surrogate_text_async(
        item,
        use_vlm_for_images=True,
        vlm_semaphore=None,
    )
    assert "Visual summary:" not in text
    assert "Only caption" in text


@pytest.mark.asyncio
async def test_content_item_async_image_decorative_skips_summary(monkeypatch, tmp_path) -> None:
    async def filtered(_b64: str, _mime: str) -> str:
        return _VISION_SKIPPED_DECORATIVE

    monkeypatch.setattr(
        "rag_mvp.multimodal_surrogate_chunks._call_vision_image_summary",
        filtered,
    )
    p = tmp_path / "three.png"
    p.write_bytes(_MIN_PNG)
    item = {"type": "image", "img_path": str(p)}
    text = await content_item_to_surrogate_text_async(
        item,
        use_vlm_for_images=True,
        vlm_semaphore=None,
    )
    assert "Visual summary:" not in text
    assert "[Image]" in text


@pytest.mark.asyncio
async def test_multimodal_items_async_with_vlm_mock(monkeypatch, tmp_path) -> None:
    async def fake_summary(_b64: str, _mime: str) -> str:
        return "Short visual note."

    monkeypatch.setattr(
        "rag_mvp.multimodal_surrogate_chunks._call_vision_image_summary",
        fake_summary,
    )
    p = tmp_path / "m.png"
    p.write_bytes(_MIN_PNG)
    tok = TiktokenTokenizer(model_name="gpt-4o-mini")
    lr = SimpleNamespace(
        tokenizer=tok,
        chunk_overlap_token_size=10,
        chunk_token_size=200,
    )
    mm = [{"type": "image", "img_path": str(p), "image_caption": ["C"]}]
    chunks = await multimodal_items_to_custom_chunks_async(
        lr,  # type: ignore[arg-type]
        mm,
        "doc.pdf",
        order_base=0,
        use_vlm_for_images=True,
    )
    assert len(chunks) >= 1
    assert "Visual summary:" in chunks[0]["content"]
    assert "Short visual note" in chunks[0]["content"]
