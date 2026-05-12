"""Worker routes ``index_only`` to ``process_index_only``."""

from __future__ import annotations

from unittest.mock import MagicMock

import rag_mvp.worker as worker


def test_process_one_index_only_calls_process_index_only(monkeypatch) -> None:
    called: list[tuple[str, bool, bool]] = []

    def fake_process_index_only(
        conn: object, material_id: str, *, text_only: bool = True, skip_kg: bool = True
    ) -> None:
        called.append((material_id, text_only, skip_kg))

    monkeypatch.setattr(worker, "process_index_only", fake_process_index_only)
    conn = MagicMock()
    worker._process_one(
        conn,
        {
            "operation": "index_only",
            "material_id": "11111111-1111-1111-1111-111111111111",
            "text_only": "false",
        },
    )
    assert called == [("11111111-1111-1111-1111-111111111111", False, True)]


def test_process_one_index_only_passes_skip_kg(monkeypatch) -> None:
    called: list[tuple[str, bool, bool]] = []

    def fake_process_index_only(
        conn: object, material_id: str, *, text_only: bool = True, skip_kg: bool = True
    ) -> None:
        called.append((material_id, text_only, skip_kg))

    monkeypatch.setattr(worker, "process_index_only", fake_process_index_only)
    conn = MagicMock()
    worker._process_one(
        conn,
        {
            "operation": "index_only",
            "material_id": "11111111-1111-1111-1111-111111111111",
            "text_only": "true",
            "skip_kg": "false",
        },
    )
    assert called == [("11111111-1111-1111-1111-111111111111", True, False)]
