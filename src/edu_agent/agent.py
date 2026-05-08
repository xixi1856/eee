"""EduAgent: single-turn and multi-turn conversational loop.

Follows the Hermes-style ReAct pattern:
  1. Build system prompt.
  2. Call LLM (with tool schemas).
  3. If LLM returns tool_calls → execute tools → append results → go to 2.
  4. If LLM returns a final message → append → return to caller.
  5. Abort after max_iterations to prevent infinite loops.

The agent is intentionally synchronous so it can be used directly from CLI
code without managing an event loop externally.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, cast

from openai import OpenAI

from edu_agent.registry import registry as _registry
from edu_agent.learner_profile import load_profile, profile_summary
from edu_agent.prompt_builder import build_system_prompt
from edu_agent.registry import discover_builtin_tools
from edu_agent.safety import check_input, check_output
from edu_agent.session_store import append_message
from edu_agent.skill_tool_registry import discover_and_register
from edu_agent.skills_loader import load_skill_entries
from edu_agent.tools import TOOL_SCHEMAS
from edu_agent.types import AgentCallbacks, AgentConfig

logger = logging.getLogger(__name__)

# Finish reasons that signal the model has produced a final user-facing answer.
_STOP_REASONS = {"stop", "end", "eos", None}


class EduAgent:
    """Stateful educational agent.

    Maintains a rolling conversation history (``messages``) across multiple
    ``run_turn()`` calls within the same session.  Call ``reset()`` to start
    a fresh conversation without creating a new instance.
    """

    def __init__(self, config: AgentConfig | None = None) -> None:
        discover_builtin_tools()
        self.config = config or AgentConfig()
        if not self.config.session_id:
            self.config.session_id = uuid.uuid4().hex[:12]

        self.messages: list[dict] = []

        # Lazy-import settings here to avoid circular imports and to allow
        # tests to patch the settings object before the agent is constructed.
        from rag_mvp.config import settings as _settings

        self._client = OpenAI(
            api_key=_settings.llm_api_key,
            base_url=_settings.llm_base_url,
        )
        self._model = self.config.model or _settings.llm_model
        self._temperature = _settings.llm_temperature
        self._max_tokens = _settings.llm_max_tokens
        self._skills_dir: str | Path = self.config.skills_dir
        self._skill_entries = load_skill_entries(self._skills_dir)

        # Load learner profile and cache its summary for prompt injection.
        self._profile = load_profile(
            self.config.user_id,
            storage_dir=self.config.profile_storage_dir,
        )
        self._profile_summary: str = profile_summary(self._profile)

        # Optional event hooks — set by CLI after construction.
        self.callbacks: AgentCallbacks | None = None

        # Auto-register script-backed skills as callable tools (Hermes Level-0).
        _registered = discover_and_register(self._skill_entries)
        if _registered:
            logger.info("Auto-registered skill tools: %s", _registered)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_turn(self, user_input: str) -> str:
        """Process one user message and return the agent's final reply.

        The conversation history (``self.messages``) is updated in-place so
        subsequent calls continue the same session.

        Args:
            user_input: Raw text from the user.

        Returns:
            The agent's final text response for this turn.
        """
        # --- Input safety gate ---
        input_check = check_input(user_input)
        if not input_check.safe:
            logger.warning(
                "Input blocked [%s]: %.80s", input_check.categories, user_input
            )
            block_msg = input_check.block_message()
            user_msg = {"role": "user", "content": user_input}
            asst_msg = {"role": "assistant", "content": block_msg}
            self.messages.append(user_msg)
            self.messages.append(asst_msg)
            self._persist_message(user_msg)
            self._persist_message(asst_msg)
            return block_msg

        user_msg = {"role": "user", "content": user_input}
        self.messages.append(user_msg)
        self._persist_message(user_msg)
        system_prompt = build_system_prompt(
            skills_dir=self._skills_dir,
            learner_profile_summary=self._profile_summary,
            available_tools={s["function"]["name"] for s in TOOL_SCHEMAS},
            skill_entries=self._skill_entries,
        )

        for iteration in range(1, self.config.max_iterations + 1):
            logger.debug("Iteration %d/%d", iteration, self.config.max_iterations)

            self._safe_cb(self.callbacks and self.callbacks.on_thinking_start)

            content, finish_reason, tool_calls = self._llm_call(
                system_prompt=system_prompt,
            )

            if finish_reason == "tool_calls" or tool_calls:
                self._handle_tool_calls_from_stream(tool_calls)
                continue  # next LLM call with tool results appended

            # --- Final answer ---
            # Output safety gate
            output_check = check_output(content)
            if not output_check.safe:
                logger.warning(
                    "Output blocked [%s]", output_check.categories
                )
                content = "抱歉，我无法提供相关内容。请换一个学习问题来问我。"

            asst_msg = {"role": "assistant", "content": content}
            self.messages.append(asst_msg)
            self._persist_message(asst_msg)
            logger.debug("Agent replied after %d iteration(s)", iteration)
            return content

        # Iteration budget exhausted
        budget_msg = "抱歉，当前问题需要更多推理步骤。请尝试将问题拆分为更小的部分重新提问。"
        asst_msg = {"role": "assistant", "content": budget_msg}
        self.messages.append(asst_msg)
        self._persist_message(asst_msg)
        return budget_msg

    def reset(self) -> None:
        """Clear conversation history while keeping the same session config."""
        self.messages = []
        logger.debug("Conversation history cleared for session %s", self.config.session_id)

    def reload_skills(self) -> None:
        """Invalidate the in-memory skill file cache.

        The next call to ``run_turn()`` will re-read all skill Markdown files
        from disk, picking up any edits made since the agent was started.
        """
        from edu_agent.skills_loader import invalidate_cache
        invalidate_cache()
        self._skill_entries = load_skill_entries(self._skills_dir)
        logger.debug("Skill cache invalidated for session %s", self.config.session_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_cb(fn, *args) -> None:
        """Call an optional callback, swallowing any exception."""
        if fn is None:
            return
        try:
            fn(*args)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Callback %s raised: %s", fn, exc)

    def _llm_call(
        self,
        system_prompt: str,
    ) -> tuple[str, str | None, list[dict]]:
        """Unified LLM call — streaming when ``callbacks.on_text_chunk`` is set.

        Returns
        -------
        (content, finish_reason, tool_calls)
            ``tool_calls`` is a list of plain dicts with keys
            ``{id, name, arguments}`` ready for ``_handle_tool_calls_from_stream``.
        """
        cb = self.callbacks
        use_stream = cb is not None and cb.on_text_chunk is not None

        api_messages = cast(
            list[Any],
            [{"role": "system", "content": system_prompt}] + self.messages,
        )

        if not use_stream:
            # ── Non-streaming path (original behaviour) ───────────────────
            response = self._client.chat.completions.create(
                model=self._model,
                messages=api_messages,
                tools=cast(list[Any], TOOL_SCHEMAS),
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
            choice = response.choices[0]
            msg: ChatCompletionMessage = choice.message
            tool_calls_raw: list[dict] = []
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    tool_calls_raw.append({
                        "id": tc.id,
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                        "_tc_obj": tc,  # keep original for model_dump
                    })
            return msg.content or "", choice.finish_reason, tool_calls_raw

        # ── Streaming path ────────────────────────────────────────────────
        stream = self._client.chat.completions.create(
            model=self._model,
            messages=api_messages,
            tools=cast(list[Any], TOOL_SCHEMAS),
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            stream=True,
        )

        content_parts: list[str] = []
        # Accumulate tool_calls deltas: index → {id, name, arguments}
        tc_acc: dict[int, dict] = {}
        finish_reason: str | None = None
        thinking_ended = False

        for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if chunk.choices:
                finish_reason = chunk.choices[0].finish_reason or finish_reason

            if delta is None:
                continue

            # ── text delta ────────────────────────────────────────────────
            if delta.content:
                if not thinking_ended:
                    self._safe_cb(cb.on_thinking_end)
                    thinking_ended = True
                content_parts.append(delta.content)
                self._safe_cb(cb.on_text_chunk, delta.content)

            # ── tool_calls delta ──────────────────────────────────────────
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tc_acc:
                        tc_acc[idx] = {"id": "", "name": "", "arguments": ""}
                    if tc_delta.id:
                        tc_acc[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tc_acc[idx]["name"] += tc_delta.function.name
                        if tc_delta.function.arguments:
                            tc_acc[idx]["arguments"] += tc_delta.function.arguments

        # Signal end of thinking if not already done (tool_calls path)
        if not thinking_ended:
            self._safe_cb(cb.on_thinking_end)

        tool_calls_list = [tc_acc[i] for i in sorted(tc_acc)]
        return "".join(content_parts), finish_reason, tool_calls_list

    def _persist_message(self, message: dict) -> None:
        """Persist a single OpenAI-compatible message to the session JSONL."""
        try:
            append_message(
                session_id=self.config.session_id,
                user_id=self.config.user_id,
                message=message,
                storage_dir=self.config.session_storage_dir,
            )
        except OSError as exc:
            logger.error("Failed to persist message: %s", exc)

    def _handle_tool_calls_from_stream(
        self,
        tool_calls: list[dict],
    ) -> None:
        """Execute tool calls collected from streaming/non-streaming response.

        ``tool_calls`` is a list of ``{id, name, arguments[, _tc_obj]}`` dicts
        produced by ``_llm_call``.
        """
        # Reconstruct the assistant tool-call message for the API history.
        tc_message_content = []
        for tc in tool_calls:
            tc_message_content.append({
                "id": tc["id"],
                "type": "function",
                "function": {"name": tc["name"], "arguments": tc["arguments"]},
            })
        asst_tool_msg = {
            "role": "assistant",
            "content": None,
            "tool_calls": tc_message_content,
        }
        self.messages.append(asst_tool_msg)
        self._persist_message(asst_tool_msg)

        cb = self.callbacks
        for tc in tool_calls:
            tool_name = tc["name"]
            try:
                args = json.loads(tc["arguments"])
            except json.JSONDecodeError:
                args = {}

            self._safe_cb(cb and cb.on_tool_start, tool_name, args)
            logger.info("Calling tool: %s(%s)", tool_name, args)

            t0 = time.monotonic()
            result_content: str = _registry.dispatch(tool_name, args)
            duration = time.monotonic() - t0
            result_success = '"error"' not in result_content

            logger.info(
                "Tool %s → success=%s, content_len=%d",
                tool_name,
                result_success,
                len(result_content),
            )
            self._safe_cb(cb and cb.on_tool_end, tool_name, args, result_content, duration)

            tool_msg = {
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result_content,
            }
            self.messages.append(tool_msg)
            self._persist_message(tool_msg)
