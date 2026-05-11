"""Unit tests for `rag query --image` helpers (MIME + data URI)."""

import base64
from pathlib import Path

from rag_mvp.llm import build_data_uri_from_image_path, image_mime_type_for_suffix


def test_image_mime_type_for_suffix() -> None:
    assert image_mime_type_for_suffix(".PNG") == "image/png"
    assert image_mime_type_for_suffix(".jpeg") == "image/jpeg"
    assert image_mime_type_for_suffix(".jpg") == "image/jpeg"


def test_build_data_uri_from_image_path(tmp_path: Path) -> None:
    p = tmp_path / "x.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    uri = build_data_uri_from_image_path(p)
    assert uri.startswith("data:image/png;base64,")
    rest = uri.split(",", 1)[1]
    assert base64.standard_b64decode(rest) == p.read_bytes()


def test_build_data_uri_jpeg_suffix(tmp_path: Path) -> None:
    p = tmp_path / "y.jpeg"
    p.write_bytes(b"\xff\xd8\xff\xe0")
    uri = build_data_uri_from_image_path(p)
    assert uri.startswith("data:image/jpeg;base64,")
