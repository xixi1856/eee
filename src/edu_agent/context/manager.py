"""Orchestrates SessionStore, token engine, and compression (no SQL here)."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from edu_agent.config import EduSettings
from edu_agent.context.calculator import estimate_tokens, estimate_messages_tokens, get_context_limit
from edu_agent.context.compressor import (
    COMPACTION_FAILURE_SNIPPET,
    ContextOverflowError,
    compress_messages,
    format_compaction_summary_body,
    sanitize_tool_pairs,
    trim_until_under_token_limit,
)
from edu_agent.context.engine import TokenBudgetEngine
from edu_agent.context.models import ContextConfig
from edu_agent.sessions.store import SessionStore

logger = logging.getLogger(__name__)


class ContextManager:
    def __init__(
        self,
        store: SessionStore,
        config: ContextConfig,
        settings: EduSettings,
        *,
        model_name: str,
        summarizer: Callable[[list[dict[str, Any]]], str | None] | None = None,
    ) -> None:
        self._store = store
        self._config = config
        self._settings = settings
        self._model_name = model_name
        self._summarizer = summarizer
        self._engine = TokenBudgetEngine(config)
        self._summary_cooldown_until = 0.0
        self._previous_summary_text: str = ""

    @property
    def config(self) -> ContextConfig:
        return self._config

    @property
    def engine(self) -> TokenBudgetEngine:
        return self._engine

    def update_from_llm_usage(
        self,
        prompt_tokens: int | None,
        completion_tokens: int | None = None,
    ) -> None:
        self._engine.update_from_llm_usage(prompt_tokens, completion_tokens)

    def load_context(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._store.list_messages(session_id, limit=50_000, offset=0)
        return [m.to_openai_dict() for m in rows]

    def add_message(self, session_id: str, message: dict[str, Any]) -> None:
        m = dict(message)
        tc = estimate_tokens(m, self._model_name)
        m["_token_count"] = tc
        self._store.append_message(session_id, m)

    def record_compaction_failure(self, session_id: str, detail: str) -> None:
        """Persist or refresh a visible system marker after compaction pipeline failure."""
        inner = (
            f"{COMPACTION_FAILURE_SNIPPET} ({detail}). "
            "The transcript may still exceed the safe context window until compaction succeeds."
        )
        full = format_compaction_summary_body(inner)
        for row in self._store.tail_messages(session_id, 48):
            if row.metadata.role != "system" or not row.metadata.is_summary:
                continue
            c = row.content if isinstance(row.content, str) else ""
            if COMPACTION_FAILURE_SNIPPET in c:
                self._store.update_message(session_id, row.metadata.id, {"content": full})
                return
        self.add_message(session_id, {"role": "system", "content": full, "_is_summary": True})

    def check_and_compress(self, session_id: str, *, force: bool = False) -> None:
        if not self._config.compression_enabled:
            return
        messages = self.load_context(session_id)
        if not messages:
            return
        if not force and not self._engine.should_compress(messages, model_name=self._model_name):
            return

        limit = get_context_limit(self._model_name, self._config)
        now = time.monotonic()
        use_llm = self._engine.should_call_llm_summarizer(messages, model_name=self._model_name)
        if now < self._summary_cooldown_until:
            use_llm = False

        llm_summary_ok = False

        def _summarize(middle: list[dict[str, Any]]) -> str | None:
            nonlocal llm_summary_ok
            if not use_llm or self._summarizer is None:
                return None
            base = middle
            prev = (self._previous_summary_text or "").strip()
            if prev:
                base = [
                    {
                        "role": "system",
                        "content": (
                            "Previous context compaction summary — merge updates; keep sections "
                            "(Goals, Progress, Key facts, Next steps):\n"
                            + prev
                        ),
                    },
                    *middle,
                ]
            try:
                out = self._summarizer(base)
                if out and str(out).strip():
                    self._summary_cooldown_until = 0.0
                    llm_summary_ok = True
                    return out
                return None
            except Exception as exc:  # noqa: BLE001
                logger.warning("Summarizer failed: %s", exc)
                self._summary_cooldown_until = time.monotonic() + float(
                    self._config.summary_failure_cooldown_sec
                )
                return None

        compressed = compress_messages(
            messages,
            token_limit=limit,
            model_name=self._model_name,
            summarizer=_summarize,
            use_llm_summarizer=use_llm,
            protect_last_rounds=self._config.protect_last_rounds,
            protect_first_n=self._config.protect_first_n,
            compression_target_ratio=self._config.compression_target_ratio,
            protect_last_n_messages=self._config.protect_last_n_messages,
            tool_prune_min_chars=self._config.tool_prune_min_chars,
            tool_prune_placeholder=self._config.tool_prune_placeholder,
            max_tool_chains_pulled_into_tail=self._config.max_tool_chains_pulled_into_tail,
        )
        trim_until_under_token_limit(
            compressed,
            token_limit=limit,
            model_name=self._model_name,
            protect_last_rounds=self._config.protect_last_rounds,
            protect_first_n=self._config.protect_first_n,
        )
        sanitize_tool_pairs(compressed)
        # Hard ceiling: model context (provider max), not the softer context_token budget.
        if estimate_messages_tokens(compressed, self._model_name) > int(
            self._config.model_max_tokens * 0.98
        ):
            raise ContextOverflowError(
                "Conversation still exceeds model_max_tokens after compaction; "
                "raise limits or shorten the session manually."
            )
        for m in compressed:
            if m.get("_is_summary"):
                txt = (m.get("content") or "").strip()
                if llm_summary_ok and txt:
                    self._previous_summary_text = txt
                break

        counts = [estimate_tokens(m, self._model_name) for m in compressed]
        # Strip internal keys before persist; keep summary marker for DB is_summary column.
        clean = []
        for m in compressed:
            d = {k: v for k, v in m.items() if not k.startswith("_")}
            if m.get("_is_summary"):
                d["_is_summary"] = True
            clean.append(d)
        self._store.replace_session_messages(session_id, clean, token_counts=counts)
