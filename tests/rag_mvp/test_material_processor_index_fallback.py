"""``process_index_only`` falls back to full parse when local MinerU output is missing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import rag_mvp.material_processor as mp


def test_index_only_fallback_moves_to_parsing_and_runs_full_pipeline(monkeypatch) -> None:
    claimed = {
        "course_id": "22222222-2222-2222-2222-222222222222",
        "original_filename": "doc.pdf",
        "minio_path": "materials/m1/original.pdf",
        "file_type": "pdf",
    }
    monkeypatch.setattr(
        mp,
        "_claim_material_for_index_retry",
        lambda _conn, _mid: claimed,
    )
    monkeypatch.setattr(mp, "_parse_output_has_content_list", lambda _mid: False)

    full_calls: list[tuple] = []

    def fake_run(
        conn,
        material_id,
        course_id,
        minio_path,
        file_type,
        original_filename,
        text_only,
        skip_kg,
    ) -> None:
        full_calls.append(
            (material_id, course_id, minio_path, file_type, original_filename, text_only, skip_kg),
        )

    monkeypatch.setattr(mp, "_run_material_download_parse_and_ingest", fake_run)

    status_updates: list[tuple] = []

    def fake_update(conn, material_id, status, status_message=None, indexed_chunk_count=None, *, expect_status_in=None):
        status_updates.append((status, expect_status_in))
        return True

    monkeypatch.setattr(mp, "update_material_status", fake_update)

    conn = MagicMock()
    mid = "11111111-1111-1111-1111-111111111111"
    mp.process_index_only(conn, mid)

    assert ("PARSING", ("INDEXING",)) in status_updates
    assert full_calls == [
        (
            mid,
            claimed["course_id"],
            claimed["minio_path"],
            claimed["file_type"],
            claimed["original_filename"],
            True,
            True,
        ),
    ]


def test_index_only_ingest_only_when_parse_cache_present(monkeypatch) -> None:
    claimed = {
        "course_id": "22222222-2222-2222-2222-222222222222",
        "original_filename": "doc.pdf",
        "minio_path": "materials/m1/original.pdf",
        "file_type": "pdf",
    }
    monkeypatch.setattr(
        mp,
        "_claim_material_for_index_retry",
        lambda _conn, _mid: claimed,
    )
    monkeypatch.setattr(mp, "_parse_output_has_content_list", lambda _mid: True)

    full_calls: list[object] = []
    monkeypatch.setattr(
        mp,
        "_run_material_download_parse_and_ingest",
        lambda *a, **k: full_calls.append("run"),
    )

    monkeypatch.setattr(mp, "_delete_material_rag_dispatch", lambda *_a, **_k: None)
    monkeypatch.setattr(mp, "_ingest_parsed_dispatch", lambda *_a, **_k: 3)

    def fake_update(conn, material_id, status, status_message=None, indexed_chunk_count=None, *, expect_status_in=None):
        return True

    monkeypatch.setattr(mp, "update_material_status", fake_update)

    conn = MagicMock()
    mp.process_index_only(conn, "11111111-1111-1111-1111-111111111111")

    assert full_calls == []


def test_index_only_fallback_failed_when_cannot_move_to_parsing(monkeypatch) -> None:
    claimed = {
        "course_id": "22222222-2222-2222-2222-222222222222",
        "original_filename": None,
        "minio_path": "x.pdf",
        "file_type": "pdf",
    }
    monkeypatch.setattr(
        mp,
        "_claim_material_for_index_retry",
        lambda _conn, _mid: claimed,
    )
    monkeypatch.setattr(mp, "_parse_output_has_content_list", lambda _mid: False)

    full_calls: list[object] = []
    monkeypatch.setattr(
        mp,
        "_run_material_download_parse_and_ingest",
        lambda *a, **k: full_calls.append("run"),
    )

    updates: list[tuple] = []

    def fake_update(conn, material_id, status, status_message=None, indexed_chunk_count=None, *, expect_status_in=None):
        updates.append((status, status_message, expect_status_in))
        if status == "PARSING" and expect_status_in == ("INDEXING",):
            return False
        return True

    monkeypatch.setattr(mp, "update_material_status", fake_update)

    conn = MagicMock()
    mp.process_index_only(conn, "11111111-1111-1111-1111-111111111111")

    assert full_calls == []
    assert ("FAILED", "RETRY_STATE_LOST", ("INDEXING",)) in updates


def test_upload_preview_pdf_with_verify_retries_until_visible(monkeypatch) -> None:
    upload_calls: list[tuple[Path, str, str | None]] = []
    exists_checks = iter([False, True])

    monkeypatch.setattr(
        mp,
        "_upload_object",
        lambda p, k, *, content_type=None: upload_calls.append((p, k, content_type)),
    )
    monkeypatch.setattr(mp, "_object_exists", lambda _k: next(exists_checks))
    monkeypatch.setattr(mp.time, "sleep", lambda _s: None)

    pdf_file = Path("preview.pdf")
    preview_key = "materials/course/material/preview.pdf"
    mp._upload_preview_pdf_with_verify(pdf_file, preview_key, max_attempts=3)

    assert upload_calls == [
        (pdf_file, preview_key, "application/pdf"),
        (pdf_file, preview_key, "application/pdf"),
    ]


def test_upload_preview_pdf_with_verify_raises_after_max_attempts(monkeypatch) -> None:
    upload_calls: list[tuple[Path, str, str | None]] = []

    monkeypatch.setattr(
        mp,
        "_upload_object",
        lambda p, k, *, content_type=None: upload_calls.append((p, k, content_type)),
    )
    monkeypatch.setattr(mp, "_object_exists", lambda _k: False)
    monkeypatch.setattr(mp.time, "sleep", lambda _s: None)

    pdf_file = Path("preview.pdf")
    preview_key = "materials/course/material/preview.pdf"
    try:
        mp._upload_preview_pdf_with_verify(pdf_file, preview_key, max_attempts=2)
    except RuntimeError as exc:
        assert "upload verify failed" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")

    assert upload_calls == [
        (pdf_file, preview_key, "application/pdf"),
        (pdf_file, preview_key, "application/pdf"),
    ]


def test_maybe_enqueue_convert_preview_reconciles_when_preview_exists(monkeypatch) -> None:
    mid = "11111111-1111-1111-1111-111111111111"
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchone.return_value = (
        "pptx",
        "UPLOADED",
        "PENDING",
        "materials/course/mid/slides.pptx",
    )
    conn.cursor.return_value.__enter__.return_value = cur

    reconciled: list[tuple[str, str]] = []
    parse_enqueued: list[tuple[str, bool, bool]] = []
    convert_enqueued: list[str] = []

    monkeypatch.setattr(mp, "_object_exists", lambda _k: True)
    monkeypatch.setattr(
        mp,
        "update_material_preview_pdf_status",
        lambda _conn, material_id, status, _status_message=None: reconciled.append((material_id, status)),
    )
    monkeypatch.setattr(
        mp,
        "_enqueue_parse_and_index_task",
        lambda material_id, *, text_only, skip_kg: parse_enqueued.append((material_id, text_only, skip_kg)),
    )
    monkeypatch.setattr(
        mp,
        "_enqueue_convert_preview_task",
        lambda material_id, *, text_only, skip_kg: convert_enqueued.append(material_id),
    )

    mp._maybe_enqueue_convert_preview_for_stuck_office(
        conn,
        mid,
        text_only=False,
        skip_kg=False,
    )

    assert reconciled == [(mid, "READY")]
    assert parse_enqueued == [(mid, False, False)]
    assert convert_enqueued == []


def test_convert_preview_reconciles_when_preview_exists(monkeypatch) -> None:
    mid = "11111111-1111-1111-1111-111111111111"
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchone.return_value = (
        "pptx",
        "UPLOADED",
        "PENDING",
        "materials/course/mid/slides.pptx",
    )
    conn.cursor.return_value.__enter__.return_value = cur

    reconciled: list[tuple[str, str]] = []
    parse_enqueued: list[tuple[str, bool, bool]] = []

    monkeypatch.setattr(mp, "_object_exists", lambda _k: True)
    monkeypatch.setattr(
        mp,
        "update_material_preview_pdf_status",
        lambda _conn, material_id, status, _status_message=None: reconciled.append((material_id, status)),
    )
    monkeypatch.setattr(
        mp,
        "_enqueue_parse_and_index_task",
        lambda material_id, *, text_only, skip_kg: parse_enqueued.append((material_id, text_only, skip_kg)),
    )
    monkeypatch.setattr(
        mp,
        "_try_claim_convert_preview_row",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("should not claim when preview already exists")),
    )

    mp.process_convert_preview(conn, mid, text_only=False, skip_kg=True)

    assert reconciled == [(mid, "READY")]
    assert parse_enqueued == [(mid, False, True)]
