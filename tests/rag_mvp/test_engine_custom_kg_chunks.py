"""Tests for chunking used in skip-entity (``ainsert_custom_kg``) ingest path."""

from __future__ import annotations

from types import SimpleNamespace

from lightrag.utils import TiktokenTokenizer

from rag_mvp.engine import _custom_kg_chunks_from_text


def test_custom_kg_chunks_from_text_splits_long_content() -> None:
    tok = TiktokenTokenizer(model_name="gpt-4o-mini")
    lr = SimpleNamespace(
        tokenizer=tok,
        chunk_overlap_token_size=50,
        chunk_token_size=120,
    )
    text = ("word " * 400).strip()
    chunks = _custom_kg_chunks_from_text(lr, text, "mat_x_test.pdf")  # type: ignore[arg-type]
    assert len(chunks) >= 2
    assert all(c["file_path"] == "mat_x_test.pdf" for c in chunks)
    assert all("source_id" in c and "chunk_order_index" in c for c in chunks)
    assert chunks[0]["chunk_order_index"] == 0


def test_custom_kg_chunks_from_text_empty_input() -> None:
    tok = TiktokenTokenizer(model_name="gpt-4o-mini")
    lr = SimpleNamespace(
        tokenizer=tok,
        chunk_overlap_token_size=10,
        chunk_token_size=100,
    )
    assert _custom_kg_chunks_from_text(lr, "   \n\t  ", "fp") == []  # type: ignore[arg-type]
