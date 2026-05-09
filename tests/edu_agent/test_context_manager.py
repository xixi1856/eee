"""Tests for token estimation and ContextManager / compressor behaviour."""

from __future__ import annotations

import pytest

from edu_agent.config import (
    AgentDefaults,
    EduSettings,
    ProviderCredentials,
    ProvidersSettings,
    RuntimeSettings,
    ToolsSettings,
)
from edu_agent.context.calculator import (
    estimate_messages_tokens,
    estimate_messages_tokens_rough,
    get_context_limit,
)
from edu_agent.context.compressor import (
    compress_messages,
    sanitize_tool_pairs,
    trim_until_under_token_limit,
)
from edu_agent.context.engine import TokenBudgetEngine
from edu_agent.context.manager import ContextManager
from edu_agent.context.models import ContextConfig
from edu_agent.sessions.store import SessionStore


@pytest.fixture()
def settings(tmp_path) -> EduSettings:
    root = tmp_path / "ws"
    root.mkdir()
    return EduSettings(
        agent=AgentDefaults(
            workspace=root,
            model="gpt-4o-mini",
            provider="openai",
            max_tokens=4096,
        ),
        providers=ProvidersSettings(
            entries={"openai": ProviderCredentials(api_key="x", base_url="https://example.invalid/v1")}
        ),
        tools=ToolsSettings(),
        runtime=RuntimeSettings(),
    )


def test_rough_vs_tiktoken_order(settings: EduSettings) -> None:
    msgs = [{"role": "user", "content": "hello " * 200}]
    rough = estimate_messages_tokens_rough(msgs)
    precise = estimate_messages_tokens(msgs, "gpt-4o-mini")
    assert rough > 0 and precise > 0


def test_get_context_limit(settings: EduSettings) -> None:
    cfg = ContextConfig(model_max_tokens=2000, token_limit_percent=0.6)
    assert get_context_limit("any", cfg) == 1200


def test_engine_update_from_usage(settings: EduSettings) -> None:
    cfg = ContextConfig(model_max_tokens=1000, token_limit_percent=0.5)
    eng = TokenBudgetEngine(cfg)
    eng.update_from_llm_usage(900, 50)
    rough_msgs = [{"role": "user", "content": "short"}]
    assert eng.display_tokens(rough_msgs) >= 900


def test_compress_static_fallback_when_summarizer_none(settings: EduSettings) -> None:
    many_users = [{"role": "user", "content": f"turn-{i}"} for i in range(8)]
    many_asst = [{"role": "assistant", "content": f"reply-{i}"} for i in range(8)]
    messages: list[dict] = []
    for i in range(8):
        messages.append(many_users[i])
        messages.append(many_asst[i])
    out = compress_messages(
        messages,
        token_limit=500,
        model_name="gpt-4o-mini",
        summarizer=None,
        use_llm_summarizer=False,
        protect_last_rounds=3,
    )
    assert any(m.get("role") == "system" for m in out)
    sys0 = next(m for m in out if m.get("role") == "system")
    c = sys0.get("content") or ""
    assert "[CONTEXT COMPACTION]" in c
    assert "REFERENCE ONLY" in c
    assert "END OF CONTEXT SUMMARY" in c


def test_context_manager_compression_persists(settings: EduSettings, tmp_path) -> None:
    db = tmp_path / "s.db"
    store = SessionStore(db)
    cfg = ContextConfig(
        # High ceiling so post-compaction tail (last 3 rounds) can still fit the overflow check.
        model_max_tokens=64_000,
        token_limit_percent=0.08,
        compression_enabled=True,
        summary_trigger_multiplier=1.0,
    )
    mgr = ContextManager(
        store,
        cfg,
        settings,
        model_name="gpt-4o-mini",
        summarizer=lambda middle: None,
    )
    s = store.create_session("u")
    sid = s.metadata.id
    filler = "word " * 400
    for i in range(6):
        store.append_message(sid, {"role": "user", "content": f"{i}:{filler}"})
        store.append_message(sid, {"role": "assistant", "content": f"a{i}:{filler}"})
    mgr.check_and_compress(sid)
    rows = store.list_messages(sid, limit=200)
    roles = [r.metadata.role for r in rows]
    assert "system" in roles


def test_compress_preserves_early_tool_chain(settings: EduSettings) -> None:
    """Tool assistant + tool result before many turns must land in tail, not only middle."""
    filler = "x" * 120
    msgs: list[dict] = [
        {"role": "user", "content": "start " + filler},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_early",
                    "type": "function",
                    "function": {"name": "demo", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_early", "content": "tool output " + filler},
    ]
    for i in range(10):
        msgs.append({"role": "user", "content": f"u{i} " + filler})
        msgs.append({"role": "assistant", "content": f"a{i} " + filler})
    out = compress_messages(
        msgs,
        token_limit=800,
        model_name="gpt-4o-mini",
        summarizer=None,
        use_llm_summarizer=False,
        protect_last_rounds=3,
        protect_last_n_messages=2,
        compression_target_ratio=0.15,
    )
    tool_ids = [m.get("tool_call_id") for m in out if m.get("role") == "tool"]
    assert "call_early" in tool_ids
    roles = [m.get("role") for m in out]
    assert roles.count("tool") >= 1


def test_trim_preserves_summary_message(settings: EduSettings) -> None:
    msgs = [
        {"role": "system", "content": "noise", "_is_summary": False},
        {"role": "system", "content": "[CONTEXT COMPACTION] summary", "_is_summary": True},
        {"role": "user", "content": "tail " + "w" * 400},
        {"role": "assistant", "content": "tail reply " + "w" * 400},
    ]
    limit = 120
    trim_until_under_token_limit(
        msgs,
        token_limit=limit,
        model_name="gpt-4o-mini",
        protect_last_rounds=2,
        protect_first_n=0,
    )
    summary_msgs = [m for m in msgs if m.get("_is_summary")]
    assert len(summary_msgs) == 1


def test_sanitize_tool_pairs_inserts_stub() -> None:
    msgs = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}},
            ],
        },
    ]
    sanitize_tool_pairs(msgs)
    assert len(msgs) == 2
    assert msgs[1].get("role") == "tool"
    assert msgs[1].get("tool_call_id") == "c1"


def test_double_compression_keeps_summary_in_db(settings: EduSettings, tmp_path) -> None:
    db = tmp_path / "dc.db"
    store = SessionStore(db)
    cfg = ContextConfig(
        model_max_tokens=64_000,
        token_limit_percent=0.08,
        compression_enabled=True,
        summary_trigger_multiplier=1.0,
        protect_last_n_messages=2,
        compression_target_ratio=0.1,
    )
    mgr = ContextManager(store, cfg, settings, model_name="gpt-4o-mini", summarizer=lambda middle: None)
    s = store.create_session("u")
    sid = s.metadata.id
    chunk = "word " * 300
    for t in range(8):
        store.append_message(sid, {"role": "user", "content": f"{t}:{chunk}"})
        store.append_message(sid, {"role": "assistant", "content": f"a{t}:{chunk}"})
    mgr.check_and_compress(sid)
    mgr.check_and_compress(sid)
    rows = store.list_messages(sid, limit=200)
    summaries = [r for r in rows if r.metadata.is_summary]
    assert len(summaries) >= 1
    sys_rows = [r for r in rows if r.metadata.role == "system"]
    assert any("[CONTEXT COMPACTION]" in (r.content or "") for r in sys_rows)
    assert any("REFERENCE ONLY" in (r.content or "") for r in sys_rows)
    assert any("END OF CONTEXT SUMMARY" in (r.content or "") for r in sys_rows)


def test_compress_max_tool_chains_cap_leaves_middle(settings: EduSettings) -> None:
    """With a low cap, not every historical tool chain is pulled into the protected tail."""
    filler = "z" * 80
    msgs: list[dict] = []
    for k in range(5):
        msgs.append({"role": "user", "content": f"u{k} {filler}"})
        msgs.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"cid{k}",
                        "type": "function",
                        "function": {"name": "f", "arguments": "{}"},
                    }
                ],
            },
        )
        msgs.append({"role": "tool", "tool_call_id": f"cid{k}", "content": f"out{k} {filler}"})
    for j in range(6):
        msgs.append({"role": "user", "content": f"tail{j} {filler}"})
        msgs.append({"role": "assistant", "content": f"ta{j} {filler}"})
    out = compress_messages(
        msgs,
        token_limit=900,
        model_name="gpt-4o-mini",
        summarizer=None,
        use_llm_summarizer=False,
        protect_last_rounds=3,
        protect_last_n_messages=2,
        compression_target_ratio=0.12,
        max_tool_chains_pulled_into_tail=1,
    )
    assert any(m.get("role") == "system" for m in out)


def test_record_compaction_failure_dedupes(settings: EduSettings, tmp_path) -> None:
    db = tmp_path / "cf.db"
    store = SessionStore(db)
    cfg = ContextConfig(model_max_tokens=8000, token_limit_percent=0.5)
    mgr = ContextManager(store, cfg, settings, model_name="gpt-4o-mini", summarizer=None)
    s = store.create_session("u")
    sid = s.metadata.id
    mgr.record_compaction_failure(sid, "first")
    mgr.record_compaction_failure(sid, "second")
    rows = store.list_messages(sid, limit=50)
    fail_rows = [r for r in rows if "Automatic context compaction failed" in (r.content or "")]
    assert len(fail_rows) == 1
    assert "second" in (fail_rows[0].content or "")
