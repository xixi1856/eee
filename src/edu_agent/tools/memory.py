"""Memory tools: remember_fact, search_memory, update_profile_note (phase3)."""

from __future__ import annotations

from edu_agent.memory.models import AssistantNote, Fact, FactSource
from edu_agent.runtime_context import get_current_runtime
from edu_agent.toolsets.models import ToolPermission, ToolSpec
from edu_agent.toolsets.registry import toolset_registry
from edu_agent.tool_payloads import tool_error, tool_result


async def _remember_fact(args: dict) -> str:
    rt = get_current_runtime()
    if not rt.memory_enabled or rt.memory_store is None or rt.memory_retriever is None:
        return tool_error("记忆系统未启用")
    content = (args.get("fact_content") or args.get("content") or "").strip()
    if not content:
        return tool_error("fact_content 不能为空")
    cat = args.get("category") or "preference"
    if cat not in (
        "concept_mastery",
        "concept_confusion",
        "preference",
        "difficulty",
        "question",
        "achievement",
    ):
        cat = "preference"
    try:
        conf = float(args.get("confidence", 0.85))
    except (TypeError, ValueError):
        conf = 0.85
    conf = max(0.0, min(1.0, conf))
    fact = Fact(
        user_id=rt.user_id,
        session_id=rt.session_id,
        category=cat,  # type: ignore[arg-type]
        content=content,
        confidence=conf,
        source=FactSource(
            session_id=rt.session_id,
            message_id="__tool_remember_fact__",
            tool_name="remember_fact",
        ),
        metadata={"origin": "tool:remember_fact"},
    )
    rt.memory_store.add_fact(fact)
    return tool_result(f"已记录事实 id={fact.id}")


async def _search_memory(args: dict) -> str:
    rt = get_current_runtime()
    if not rt.memory_enabled or rt.memory_store is None or rt.memory_retriever is None:
        return tool_error("记忆系统未启用")
    kw = (args.get("keyword") or args.get("query") or "").strip()
    if not kw:
        return tool_error("keyword 不能为空")
    concepts = rt.memory_retriever.search_concepts(rt.user_id, kw, limit=int(args.get("limit", 10)))
    lines = [f"- {c.id}: {c.name} (mastery={c.mastery_level:.2f})" for c in concepts]
    facts = rt.memory_store.search_facts(rt.user_id, kw, limit=10)
    for f in facts:
        lines.append(f"- fact {f.id[:8]}… [{f.category}] {f.content[:120]}")
    return tool_result("\n".join(lines) if lines else "（无匹配记忆）")


async def _update_profile_note(args: dict) -> str:
    rt = get_current_runtime()
    if not rt.memory_enabled or rt.memory_store is None:
        return tool_error("记忆系统未启用")
    note = (args.get("note") or "").strip()
    if not note:
        return tool_error("note 不能为空")
    profile = rt.memory_store.load_profile(rt.user_id) or rt.memory_store.default_profile(rt.user_id)
    profile.assistant_notes.append(
        AssistantNote(text=note, session_id=rt.session_id, source="tool:update_profile_note")
    )
    if len(profile.assistant_notes) > 200:
        profile.assistant_notes = profile.assistant_notes[-200:]
    rt.memory_store.save_profile(profile)
    return tool_result("画像备注已保存")


_SCHEMA_REMEMBER = {
    "name": "remember_fact",
    "description": "将一条与学习相关的明确事实写入长期记忆（Fact 层）。",
    "parameters": {
        "type": "object",
        "properties": {
            "fact_content": {"type": "string", "description": "简短客观事实描述"},
            "category": {
                "type": "string",
                "description": "可选：concept_mastery | concept_confusion | preference | difficulty | question | achievement",
            },
            "confidence": {"type": "number", "description": "0~1，默认 0.85"},
        },
        "required": ["fact_content"],
    },
}

_SCHEMA_SEARCH = {
    "name": "search_memory",
    "description": "按关键词检索已聚合的概念与相关事实摘要。",
    "parameters": {
        "type": "object",
        "properties": {
            "keyword": {"type": "string"},
            "limit": {"type": "integer", "description": "最多返回条数，默认 10"},
        },
        "required": ["keyword"],
    },
}

_SCHEMA_NOTE = {
    "name": "update_profile_note",
    "description": "向学习者画像追加一条结构化备注（带时间戳，可审计）。",
    "parameters": {
        "type": "object",
        "properties": {"note": {"type": "string"}},
        "required": ["note"],
    },
}

toolset_registry.register(
    ToolSpec(
        name=_SCHEMA_REMEMBER["name"],
        description=_SCHEMA_REMEMBER["description"],
        input_schema=_SCHEMA_REMEMBER["parameters"],
        handler=_remember_fact,
        toolset="memory",
        permissions=[ToolPermission.WRITE],
    )
)
toolset_registry.register(
    ToolSpec(
        name=_SCHEMA_SEARCH["name"],
        description=_SCHEMA_SEARCH["description"],
        input_schema=_SCHEMA_SEARCH["parameters"],
        handler=_search_memory,
        toolset="memory",
        permissions=[ToolPermission.READ],
    )
)
toolset_registry.register(
    ToolSpec(
        name=_SCHEMA_NOTE["name"],
        description=_SCHEMA_NOTE["description"],
        input_schema=_SCHEMA_NOTE["parameters"],
        handler=_update_profile_note,
        toolset="memory",
        permissions=[ToolPermission.WRITE],
    )
)
