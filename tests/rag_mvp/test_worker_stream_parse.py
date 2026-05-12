"""Pure parsing for Redis Stream XAUTOCLAIM responses (worker reliability)."""

from __future__ import annotations

from rag_mvp.worker import _parse_autoclaim_messages, _parse_bool_field


def test_parse_autoclaim_empty_cursor() -> None:
    assert _parse_autoclaim_messages(["0-0", []]) == []


def test_parse_autoclaim_flat_fields() -> None:
    resp = [
        "0-0",
        [
            [
                "123-0",
                ["task_id", "a", "material_id", "m1", "operation", "parse_and_index"],
            ],
        ],
    ]
    out = _parse_autoclaim_messages(resp)
    assert len(out) == 1
    mid, fields = out[0]
    assert mid == "123-0"
    assert fields["material_id"] == "m1"
    assert fields["operation"] == "parse_and_index"


def test_parse_autoclaim_dict_fields() -> None:
    resp = ["0-0", [["124-0", {"material_id": "m2", "operation": "delete_material"}]]]
    out = _parse_autoclaim_messages(resp)
    assert out[0][1]["material_id"] == "m2"


def test_parse_bool_field_defaults_and_values() -> None:
    assert _parse_bool_field(None, default=True) is True
    assert _parse_bool_field("", default=True) is True
    assert _parse_bool_field("true") is True
    assert _parse_bool_field("1") is True
    assert _parse_bool_field("false") is False
