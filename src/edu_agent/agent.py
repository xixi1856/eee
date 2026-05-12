"""EduAgent: single-turn and multi-turn conversational loop.

Follows the Hermes-style ReAct pattern:
  1. Build system prompt.
  2. Call LLM (with tool schemas).
  3. If LLM returns tool_calls → execute tools → append results → go to 2.
  4. If LLM returns a final message → append → return to caller.
  5. Abort after max_iterations to prevent infinite loops.

The agent's ``run_turn`` is async; CLI uses ``asyncio.run`` (or an existing loop).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from openai.types.chat import ChatCompletionMessage

from edu_agent.config import EduSettings
from edu_agent.context.calculator import estimate_messages_tokens_rough
from edu_agent.context.compressor import ContextOverflowError
from edu_agent.context.manager import ContextManager
from edu_agent.context.models import ContextConfig
from edu_agent.memory import MemoryConfig, MemoryConsolidator, MemoryExtractor, MemoryRetriever, MemoryStore
from edu_agent.memory.coordinator import MemoryCoordinator
from edu_agent.memory.manager import EduMemoryManager
from edu_agent.memory.output_scrubber import sanitize_completed_assistant_output
from edu_agent.memory.provider import BuiltinFilesystemMemoryProvider
from edu_agent.paths import build_paths
from edu_agent.bus.models import (
    AttachmentMeta,
    OutboundContentType,
    OutboundMessage,
    ensure_aware_utc,
    new_message_id,
)
from edu_agent.providers.vision import detect_vision_support
from edu_agent.providers.runtime import (
    build_async_openai_client,
    build_openai_client,
    resolve_provider_runtime,
)
from edu_agent.learner_profile import load_profile, profile_summary
from edu_agent.llm_tools import tool_specs_to_openai_tools
from edu_agent.prompt_builder import build_system_prompt
from edu_agent.runtime_context import (
    TurnRuntimeContext,
    get_current_runtime,
    reset_current_runtime,
    set_current_runtime,
)
from edu_agent.toolsets import (
    PermissionChecker,
    ToolRuntime,
    discover_builtin_tools,
    resolve_effective_permission_policy,
    toolset_registry,
)
from edu_agent.safety import check_input, check_output
from edu_agent.sessions.models import SessionStatus
from edu_agent.sessions.store import SessionArchivedError, SessionStore
from edu_agent.skills_loader import load_skill_entries
from edu_agent.types import AgentCallbacks, AgentConfig, ToolResult

logger = logging.getLogger(__name__)

# Finish reasons that signal the model has produced a final user-facing answer.
_STOP_REASONS = {"stop", "end", "eos", None}


@dataclass
class _LLMStreamState:
    """Mutable accumulator filled while draining an async LLM stream."""

    content: str = ""
    finish_reason: str | None = None
    tool_calls: list[dict] = field(default_factory=list)
    usage: tuple[int | None, int | None] = (None, None)


class EduAgent:
    """Stateful educational agent.

    Maintains a rolling conversation history (``messages``) across multiple
    ``run_turn()`` calls within the same session.  Call ``reset()`` to start
    a fresh conversation without creating a new instance.
    """

    def __init__(
        self,
        config: AgentConfig | None = None,
        settings: EduSettings | None = None,
        *,
        session_store: SessionStore | None = None,
        context_manager: ContextManager | None = None,
    ) -> None:
        if settings is None:
            raise ValueError(
                "EduAgent requires `settings=...` from edu_agent.config_loader.load_settings() "
                "at the application entrypoint."
            )
        discover_builtin_tools()
        self._settings = settings
        self.config = config or AgentConfig()
        _policy = resolve_effective_permission_policy(
            self._settings.tools.permission_policy,
            allow_network=self.config.allow_network_tools,
            allow_write=self.config.allow_write_tools,
            allow_execute=self.config.allow_execute_tools,
            allow_external=self.config.allow_external_tools,
        )
        self._permissions = PermissionChecker(
            _policy,
            approve_all=self.config.approve_all_tools,
            interactive=True,
        )
        self._tool_runtime = ToolRuntime(
            toolset_registry,
            self._settings,
            self._permissions,
        )
        self._mcp_registered = False

        self.messages: list[dict] = []
        self._course_material_cache: dict[str, list[str]] = {}

        self._paths = build_paths(
            settings,
            workspace=self.config.workspace or None,
            skills_dir=self.config.skills_dir or None,
        )
        self._main_runtime = resolve_provider_runtime(settings, self.config, "main")
        self._client = build_openai_client(self._main_runtime)
        self._async_client = build_async_openai_client(self._main_runtime)
        self._model = self._main_runtime.model
        self._temperature = self._main_runtime.temperature
        self._max_tokens = self._main_runtime.max_tokens
        self._llm_extra_body = (
            dict(self._main_runtime.llm_extra_body) if self._main_runtime.llm_extra_body else {}
        )
        self._skills_dir: Path = self._paths.skills_dir
        self._skill_entries = load_skill_entries(self._skills_dir)

        self._session_store = session_store
        self._context = None
        if session_store is not None:
            if not self.config.session_id:
                sess = session_store.create_session(self.config.user_id)
                self.config.session_id = sess.metadata.id
            else:
                existing = session_store.get_session(self.config.session_id)
                if existing is None:
                    raise ValueError(f"Unknown session_id: {self.config.session_id}")
            if context_manager is not None:
                self._context = context_manager
            else:
                self._context = ContextManager(
                    session_store,
                    ContextConfig(model_max_tokens=self._max_tokens),
                    self._settings,
                    model_name=self._model,
                    summarizer=self._build_summarizer(),
                )
            self.messages = self._context.load_context(self.config.session_id)
        else:
            if not self.config.session_id:
                self.config.session_id = uuid.uuid4().hex[:12]

        self._memory_store: MemoryStore | None = None
        self._memory_retriever: MemoryRetriever | None = None
        self._memory_consolidator: MemoryConsolidator | None = None
        self._memory_coordinator: MemoryCoordinator | None = None
        self._memory_manager: EduMemoryManager | None = None
        self._memory_config = MemoryConfig()
        self._memory_mid_consolidate_done = False
        self._memory_last_extracted_seq: int = 0
        if session_store is not None and self.config.memory_enabled:
            self._memory_store = MemoryStore(self._paths.memory_dir)
            self._memory_retriever = MemoryRetriever(self._memory_store)
            _extractor = MemoryExtractor(self._main_runtime, self._settings)
            self._memory_consolidator = MemoryConsolidator(
                self._memory_store,
                _extractor,
                self._settings,
                self._memory_config,
            )
            self._memory_coordinator = MemoryCoordinator(
                self._memory_retriever,
                self._memory_config,
                self._memory_consolidator,
            )
            self._memory_manager = EduMemoryManager()
            self._memory_manager.add_provider(
                BuiltinFilesystemMemoryProvider(self._memory_coordinator)
            )
            self._memory_manager.initialize_all(
                self.config.session_id,
                user_id=self.config.user_id,
            )

        # Load learner profile and cache its summary for prompt injection.
        self._profile = load_profile(
            self.config.user_id,
            storage_dir=self._paths.profiles_dir,
            memory_store=self._memory_store,
            concepts_store=self._memory_store,
        )
        self._profile_summary: str = profile_summary(self._profile)

        # Optional event hooks — set by CLI after construction.
        self.callbacks: AgentCallbacks | None = None

    def _make_outbound(
        self,
        in_reply_to: UUID,
        *,
        content: str = "",
        content_type: OutboundContentType = OutboundContentType.TEXT,
        is_final: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> OutboundMessage:
        return OutboundMessage(
            message_id=new_message_id(),
            in_reply_to=in_reply_to,
            session_id=self.config.session_id,
            user_id=self.config.user_id,
            timestamp=ensure_aware_utc(),
            content=content,
            content_type=content_type,
            is_final=is_final,
            metadata=dict(metadata or {}),
        )

    def _reset_b3_turn_metrics(self) -> None:
        """Per user turn: timing, token usage (last LLM chunk), RAG hit lists for qa_logs / B3."""
        self._b3_turn_t0 = time.monotonic()
        self._b3_turn_prompt_tokens: int | None = None
        self._b3_turn_completion_tokens: int | None = None
        self._b3_hit_chunks: list[str] = []
        self._b3_hit_materials: list[str] = []
        self._b3_hit_sources: list[str] = []
        self._b3_seen_chunk_ids: set[str] = set()
        self._b3_seen_material_ids: set[str] = set()
        self._b3_seen_source_labels: set[str] = set()

    def _b3_turn_metadata(self) -> dict[str, Any]:
        pt, ct = self._b3_turn_prompt_tokens, self._b3_turn_completion_tokens
        total: int | None = None
        if pt is not None and ct is not None:
            total = pt + ct
        elif pt is not None:
            total = pt
        elif ct is not None:
            total = ct
        return {
            "execution_time_ms": int((time.monotonic() - self._b3_turn_t0) * 1000),
            "model_used": self._model,
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "total_tokens": total,
            "hit_chunks": list(self._b3_hit_chunks),
            "hit_materials": list(self._b3_hit_materials),
            "hit_sources": list(self._b3_hit_sources),
        }

    def _accumulate_knowledge_hits(self, tr: ToolResult) -> None:
        if tr.tool_name != "knowledge_query" or not tr.success:
            return
        pl = tr.payload
        if not isinstance(pl, list):
            return
        for item in pl:
            if not isinstance(item, dict):
                continue
            origin = item.get("origin")
            if isinstance(origin, str):
                label = origin.strip().lower()
                if label in ("course", "personal") and label not in self._b3_seen_source_labels:
                    self._b3_seen_source_labels.add(label)
                    self._b3_hit_sources.append(label)
            cid = item.get("chunk_id")
            if isinstance(cid, str):
                c = cid.strip()
                if c and c not in self._b3_seen_chunk_ids:
                    self._b3_seen_chunk_ids.add(c)
                    self._b3_hit_chunks.append(c)
            mid = item.get("material_id")
            if isinstance(mid, str):
                m = mid.strip()
                if m and m not in self._b3_seen_material_ids:
                    self._b3_seen_material_ids.add(m)
                    self._b3_hit_materials.append(m)

    async def _ensure_mcp_tools_registered(self) -> None:
        """Connect MCP servers once and register dynamic tools (best-effort)."""
        if self._mcp_registered:
            return
        self._mcp_registered = True
        try:
            from edu_agent.mcp.integration import register_mcp_servers

            await register_mcp_servers(self._settings, toolset_registry)
        except Exception as exc:  # noqa: BLE001
            logger.warning("MCP registration skipped: %s", exc)

    def _refresh_profile_cache(self) -> None:
        self._profile = load_profile(
            self.config.user_id,
            storage_dir=self._paths.profiles_dir,
            memory_store=self._memory_store,
            concepts_store=self._memory_store,
        )
        self._profile_summary = profile_summary(self._profile)

    def finalize_memory_session(self) -> None:
        """Run memory consolidation for the current session (CLI exit / shutdown)."""
        if (
            not self.config.memory_enabled
            or self._memory_consolidator is None
            or self._session_store is None
        ):
            return
        rows = self._session_store.list_messages(self.config.session_id, limit=50_000, offset=0)
        if self._memory_coordinator is not None:
            self._memory_coordinator.consolidate_session(
                self.config.user_id,
                self.config.session_id,
                rows,
                force_extract=False,
                extract_after_seq=self._memory_last_extracted_seq,
            )
            if rows:
                self._memory_last_extracted_seq = max(m.metadata.seq for m in rows)
        if self._memory_manager is not None:
            self._memory_manager.on_session_end([m.to_openai_dict() for m in rows])
        self._refresh_profile_cache()

    def _maybe_memory_threshold_consolidate(self) -> None:
        if (
            not self.config.memory_enabled
            or self._memory_coordinator is None
            or self._session_store is None
            or self._memory_mid_consolidate_done
        ):
            return
        rows = self._session_store.list_messages(self.config.session_id, limit=50_000, offset=0)
        if not rows:
            return
        if not self._memory_coordinator.should_run_threshold_consolidate(
            [m.to_openai_dict() for m in rows]
        ):
            return
        self._memory_coordinator.consolidate_session(
            self.config.user_id,
            self.config.session_id,
            rows,
            force_extract=False,
            extract_after_seq=self._memory_last_extracted_seq,
        )
        if rows:
            self._memory_last_extracted_seq = max(m.metadata.seq for m in rows)
        self._memory_mid_consolidate_done = True
        self._refresh_profile_cache()

    def _build_summarizer(self) -> Callable[[list[dict[str, Any]]], str | None]:
        """LLM-based middle summarization (injected into ContextManager)."""

        def _summarize(middle: list[dict[str, Any]]) -> str | None:
            serialized = json.dumps(middle, ensure_ascii=False, default=str)
            if len(serialized) > 120_000:
                serialized = serialized[:120_000] + "\n...[truncated]"
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Summarize the conversation excerpt for another assistant that will "
                            "continue the session. Use markdown with these sections:\n"
                            "## Goal\n## Constraints & Preferences\n## Progress (Done / In Progress / Blocked)\n"
                            "## Key Decisions\n## Relevant Files\n## Next Steps\n## Critical Context\n"
                            "Do not answer the user directly — summary only."
                        ),
                    },
                    {"role": "user", "content": serialized},
                ],
                temperature=min(self._temperature, 0.3),
                max_tokens=min(2048, self._max_tokens),
                **self._chat_completion_extra(),
            )
            msg = resp.choices[0].message
            text = (msg.content or "").strip()
            return text or None

        return _summarize

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_turn_stream(
        self,
        user_input: str,
        *,
        in_reply_to: UUID,
        attachments: tuple[AttachmentMeta, ...] = (),
    ) -> AsyncIterator[OutboundMessage]:
        """Stream outbound chunks for one user turn (A5); uses async LLM streaming."""
        self._reset_b3_turn_metrics()
        _cid = (self.config.course_id or "").strip()
        _lid = (self.config.lesson_id or "").strip()
        ctx = TurnRuntimeContext(
            settings=self._settings,
            paths=self._paths,
            provider_runtime=self._main_runtime,
            user_id=self.config.user_id,
            session_id=self.config.session_id,
            memory_enabled=bool(self.config.memory_enabled and self._memory_store is not None),
            memory_store=self._memory_store,
            memory_retriever=self._memory_retriever,
            tool_runtime=self._tool_runtime,
            permission_checker=self._permissions,
            course_id=_cid or None,
            lesson_id=_lid or None,
        )
        token = set_current_runtime(ctx)
        try:
            async for ob in self._run_turn_inner_stream(
                user_input,
                in_reply_to=in_reply_to,
                attachments=attachments,
            ):
                yield ob
        finally:
            reset_current_runtime(token)

    async def run_turn(self, user_input: str) -> str:
        """Process one user message; aggregates ``run_turn_stream`` final TEXT."""
        rid = uuid.uuid4()
        last = ""
        async for ob in self.run_turn_stream(user_input, in_reply_to=rid):
            if ob.content_type == OutboundContentType.TEXT and ob.is_final:
                last = ob.content or ""
        return last

    # ------------------------------------------------------------------
    # Multimodal helpers
    # ------------------------------------------------------------------

    async def _prepare_user_turn(
        self,
        user_input: str,
        attachments: tuple[AttachmentMeta, ...],
    ) -> dict:
        """Build the OpenAI-compatible user message dict for this turn.

        - No attachments → plain string content.
        - Vision model + image attachments → content array with image_url parts.
        - Non-vision model or non-image files → prepend OCR/parse context prefix.
        """
        if not attachments:
            return {"role": "user", "content": user_input}

        vision = await detect_vision_support(self._main_runtime)
        image_atts = [a for a in attachments if a.mime_type.startswith("image/")]
        other_atts = [a for a in attachments if not a.mime_type.startswith("image/")]

        context_parts: list[str] = []

        # Non-image files always go through text extraction regardless of vision.
        for att in other_atts:
            extracted = await self._preprocess_attachment_for_context(att)
            context_parts.append(extracted)

        if vision and image_atts:
            # Build multimodal content array for vision-capable models.
            content: list[dict] = []
            if context_parts or user_input:
                combined_text = "\n\n".join(context_parts + [user_input]) if context_parts else user_input
                content.append({"type": "text", "text": combined_text})
            for att in image_atts:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": att.presigned_url},
                })
            return {"role": "user", "content": content}

        # Non-vision path: OCR images too and prepend everything as context.
        for att in image_atts:
            extracted = await self._preprocess_attachment_for_context(att)
            context_parts.append(extracted)

        prefix = "\n\n".join(context_parts)
        combined = f"{prefix}\n\n{user_input}" if prefix else user_input
        return {"role": "user", "content": combined}

    async def _preprocess_attachment_for_context(
        self,
        att: AttachmentMeta,
    ) -> str:
        """Download an attachment and extract a text snippet (max 800 chars)."""
        import httpx

        _MIME_TO_SUFFIX = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/gif": ".gif",
            "image/webp": ".webp",
            "application/pdf": ".pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
            "text/plain": ".txt",
            "text/markdown": ".md",
        }
        suffix = _MIME_TO_SUFFIX.get(att.mime_type, "")

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(att.presigned_url)
                resp.raise_for_status()
                raw = resp.content
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to download attachment %s: %s", att.name, exc)
            return f"[附件 {att.name} 下载失败，无法提取内容]"

        text = ""
        try:
            if att.mime_type in ("text/plain", "text/markdown") or suffix in (".txt", ".md"):
                text = raw.decode("utf-8", errors="replace")
            elif att.mime_type == "application/pdf" or suffix == ".pdf":
                from io import BytesIO
                import pypdf
                reader = pypdf.PdfReader(BytesIO(raw))
                parts = [page.extract_text() or "" for page in reader.pages]
                text = "\n".join(parts)
            else:
                # For DOCX/PPTX/XLSX/images we write to a tempfile and use
                # RAGAnything's document parser — result is returned as plain text.
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp.write(raw)
                    tmp_path = Path(tmp.name)
                try:
                    from rag_mvp.engine import _build_parser  # type: ignore[import]
                    import asyncio
                    rag = _build_parser()
                    out_dir = str(tmp_path.parent / tmp_path.stem)
                    await rag.parse_document(
                        file_path=str(tmp_path),
                        output_dir=out_dir,
                    )
                    # Collect .txt output files written by MinerU.
                    out_texts = list(Path(out_dir).rglob("*.txt"))
                    if out_texts:
                        text = "\n".join(p.read_text(errors="replace") for p in out_texts)
                except Exception as exc2:  # noqa: BLE001
                    logger.warning(
                        "RAGAnything parse failed for %s: %s; skipping content", att.name, exc2
                    )
                finally:
                    tmp_path.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Text extraction failed for %s: %s", att.name, exc)
            return f"[附件 {att.name} 内容提取失败]"

        MAX_CHARS = 800
        total_len = len(text)
        snippet = text[:MAX_CHARS]
        truncated = total_len > MAX_CHARS
        header = f"[附件: {att.name} | 共 {total_len} 字符"
        if truncated:
            header += f"，以下仅展示前 {MAX_CHARS} 字符"
        header += "]"
        return f"{header}\n{snippet}"

    def _sync_fetch_course_material_names(self, course_id: str) -> list[str]:
        import httpx

        base = os.environ.get("EDU_PLATFORM_BASE_URL", "").rstrip("/")
        if not base:
            base = self._settings.platform_base_url.rstrip("/")
        key = os.environ.get("EDU_PLATFORM_INTERNAL_API_KEY", "").strip()
        if not base or len(key) < 16:
            return []

        with httpx.Client(timeout=30.0) as client:
            r = client.get(
                f"{base}/api/v1/internal/course-materials",
                params={"course_id": course_id, "user_id": self.config.user_id},
                headers={"X-Internal-Key": key},
            )
            r.raise_for_status()
            body = r.json()

        raw = body.get("materials")
        if not isinstance(raw, list):
            return []
        out: list[str] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            title = item.get("original_filename")
            if isinstance(title, str) and title.strip():
                out.append(title.strip())
        return out

    async def _get_course_material_names(self, course_id: str) -> list[str]:
        cid = course_id.strip()
        if not cid:
            return []
        cached = self._course_material_cache.get(cid)
        if cached is not None:
            return cached
        try:
            names = await asyncio.to_thread(self._sync_fetch_course_material_names, cid)
        except Exception as exc:  # noqa: BLE001
            logger.warning("course materials preload skipped: %s", exc)
            names = []
        self._course_material_cache[cid] = names
        return names

    async def _run_turn_inner_stream(
        self,
        user_input: str,
        *,
        in_reply_to: UUID,
        attachments: tuple[AttachmentMeta, ...] = (),
    ) -> AsyncIterator[OutboundMessage]:
        await self._ensure_mcp_tools_registered()

        if self._session_store is not None:
            sess = self._session_store.get_session(self.config.session_id)
            if sess is not None and sess.metadata.status == SessionStatus.ARCHIVED:
                raise SessionArchivedError(
                    f"Session {self.config.session_id} is archived (read-only)."
                )

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
            self._maybe_compress()
            yield self._make_outbound(
                in_reply_to,
                content=block_msg,
                content_type=OutboundContentType.TEXT,
                is_final=True,
                metadata=self._b3_turn_metadata(),
            )
            return

        user_msg = await self._prepare_user_turn(user_input, attachments)
        self.messages.append(user_msg)
        self._persist_message(user_msg)

        if self._context is not None:
            cfg = self._context.config
            if cfg.gateway_hygiene_enabled:
                rough = estimate_messages_tokens_rough(self.messages)
                cap = max(256, int(cfg.model_max_tokens * cfg.gateway_hygiene_ratio))
                if rough >= cap:
                    logger.warning(
                        "Gateway hygiene: rough token estimate %s >= %s%% of model_max (%s); "
                        "attempting early compaction",
                        rough,
                        int(cfg.gateway_hygiene_ratio * 100),
                        cfg.model_max_tokens,
                    )
                    self._apply_context_compression(force=True)

        memory_context = ""
        memory_injection_used = False
        if (
            self.config.memory_enabled
            and self.config.memory_inject_into_prompt
            and self._memory_manager is not None
        ):
            try:
                memory_context = self._memory_manager.prefetch_all(
                    user_input.strip()[:2000],
                    session_id=self.config.session_id,
                ).strip()
                memory_injection_used = bool(memory_context)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Memory prompt injection skipped: %s", exc)

        _disabled = frozenset(self.config.disabled_tools)
        _specs = toolset_registry.list_specs(self._settings, disabled_names=_disabled)
        _openai_tools = tool_specs_to_openai_tools(_specs)
        # Conditionally activate the multimodal_attachments skill for this turn.
        if attachments:
            _skill_entries = [
                e._replace(always_inject=True) if e.name == "multimodal_attachments" else e
                for e in self._skill_entries
            ]
        else:
            _skill_entries = self._skill_entries
        _course_material_names: list[str] = []
        if (self.config.course_id or "").strip():
            _course_material_names = await self._get_course_material_names(self.config.course_id)
        system_prompt = build_system_prompt(
            skills_dir=self._skills_dir,
            learner_profile_summary=self._profile_summary,
            available_tools={s.name for s in _specs},
            skill_entries=_skill_entries,
            memory_context=memory_context,
            course_id=self.config.course_id,
            course_material_names=_course_material_names,
        )

        for iteration in range(1, self.config.max_iterations + 1):
            logger.debug("Iteration %d/%d", iteration, self.config.max_iterations)

            self._safe_cb(self.callbacks and self.callbacks.on_thinking_start)

            state = _LLMStreamState()
            async for ob in self._llm_stream_outbounds(
                system_prompt,
                openai_tools=_openai_tools,
                in_reply_to=in_reply_to,
                state=state,
            ):
                yield ob

            if state.usage[0] is not None:
                self._b3_turn_prompt_tokens = state.usage[0]
            if state.usage[1] is not None:
                self._b3_turn_completion_tokens = state.usage[1]

            if self._context is not None and state.usage[0] is not None:
                self._context.update_from_llm_usage(
                    self.config.session_id,
                    state.usage[0],
                    state.usage[1],
                )

            content = state.content
            finish_reason = state.finish_reason
            tool_calls = state.tool_calls

            if finish_reason == "tool_calls" or tool_calls:
                async for ob in self._handle_tool_calls_stream(
                    tool_calls, _disabled, in_reply_to=in_reply_to
                ):
                    yield ob
                continue

            output_check = check_output(content)
            if not output_check.safe:
                logger.warning(
                    "Output blocked [%s]", output_check.categories
                )
                content = "抱歉，我无法提供相关内容。请换一个学习问题来问我。"
            elif self.config.memory_inject_into_prompt:
                content = sanitize_completed_assistant_output(
                    content,
                    memory_injection_used=memory_injection_used,
                )

            asst_msg = {"role": "assistant", "content": content}
            self.messages.append(asst_msg)
            self._persist_message(asst_msg)
            logger.debug("Agent replied after %d iteration(s)", iteration)
            self._maybe_compress()
            self._maybe_memory_threshold_consolidate()
            yield self._make_outbound(
                in_reply_to,
                content=content,
                content_type=OutboundContentType.TEXT,
                is_final=True,
                metadata=self._b3_turn_metadata(),
            )
            return

        budget_msg = "抱歉，当前问题需要更多推理步骤。请尝试将问题拆分为更小的部分重新提问。"
        if self.config.memory_inject_into_prompt:
            budget_msg = sanitize_completed_assistant_output(
                budget_msg,
                memory_injection_used=memory_injection_used,
            )
        asst_msg = {"role": "assistant", "content": budget_msg}
        self.messages.append(asst_msg)
        self._persist_message(asst_msg)
        self._maybe_compress()
        self._maybe_memory_threshold_consolidate()
        yield self._make_outbound(
            in_reply_to,
            content=budget_msg,
            content_type=OutboundContentType.TEXT,
            is_final=True,
            metadata=self._b3_turn_metadata(),
        )

    def reset(self) -> None:
        """Clear conversation history; with a session store, start a new session row."""
        self.messages = []
        self._memory_mid_consolidate_done = False
        self._memory_last_extracted_seq = 0
        if self._session_store is not None and self._context is not None:
            sess = self._session_store.create_session(self.config.user_id)
            self.config.session_id = sess.metadata.id
        if self._memory_manager is not None:
            self._memory_manager.initialize_all(
                self.config.session_id,
                user_id=self.config.user_id,
            )
        logger.debug("Conversation history cleared; session_id=%s", self.config.session_id)

    @property
    def has_context_manager(self) -> bool:
        return self._context is not None

    @property
    def context_compression_active(self) -> bool:
        """True when session-backed context compression can run (manager present and enabled)."""
        return bool(self._context and self._context.config.compression_enabled)

    def trigger_context_compress(self) -> None:
        """User-initiated context compaction (e.g. CLI ``/compress-context``)."""
        self._apply_context_compression(force=True)

    def _maybe_compress(self) -> None:
        self._apply_context_compression(force=False)

    def _apply_context_compression(self, *, force: bool) -> None:
        if self._context is None:
            return
        try:
            self._context.check_and_compress(self.config.session_id, force=force)
            self.messages = self._context.load_context(self.config.session_id)
        except ContextOverflowError as exc:
            logger.error("Context compaction could not fit model limit: %s", exc)
            try:
                self._context.record_compaction_failure(self.config.session_id, str(exc))
                self.messages = self._context.load_context(self.config.session_id)
            except Exception as persist_exc:  # noqa: BLE001
                logger.exception("Failed to persist compaction failure notice: %s", persist_exc)
        except Exception as exc:  # noqa: BLE001
            logger.exception("check_and_compress failed: %s", exc)
            try:
                self._context.record_compaction_failure(self.config.session_id, str(exc))
                self.messages = self._context.load_context(self.config.session_id)
            except Exception as persist_exc:  # noqa: BLE001
                logger.exception("Failed to persist compaction failure notice: %s", persist_exc)

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

    def _chat_completion_extra(self) -> dict[str, Any]:
        """Optional ``extra_body`` for OpenAI-compatible APIs (e.g. DeepSeek ``thinking`` toggle)."""
        if not self._llm_extra_body:
            return {}
        return {"extra_body": dict(self._llm_extra_body)}

    async def _llm_stream_outbounds(
        self,
        system_prompt: str,
        *,
        openai_tools: list[dict],
        in_reply_to: UUID,
        state: _LLMStreamState,
    ) -> AsyncIterator[OutboundMessage]:
        """Async LLM streaming; fills *state* and yields TEXT deltas as ``OutboundMessage``."""
        cb = self.callbacks
        api_messages = cast(
            list[Any],
            [{"role": "system", "content": system_prompt}] + self.messages,
        )
        stream = await self._async_client.chat.completions.create(
            model=self._model,
            messages=api_messages,
            tools=cast(list[Any], openai_tools),
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            stream=True,
            **self._chat_completion_extra(),
        )
        tc_acc: dict[int, dict[str, str]] = {}
        thinking_ended = False
        async for chunk in stream:
            choice = chunk.choices[0] if chunk.choices else None
            delta = choice.delta if choice else None
            if choice and choice.finish_reason:
                state.finish_reason = choice.finish_reason
            usage = getattr(chunk, "usage", None)
            if usage is not None:
                pt = getattr(usage, "prompt_tokens", None)
                ct = getattr(usage, "completion_tokens", None)
                state.usage = (pt, ct)

            if delta is None:
                continue

            if delta.content:
                if not thinking_ended:
                    self._safe_cb(cb and cb.on_thinking_end)
                    thinking_ended = True
                state.content += delta.content
                self._safe_cb(cb and cb.on_text_chunk, delta.content)
                yield self._make_outbound(
                    in_reply_to,
                    content=delta.content,
                    content_type=OutboundContentType.TEXT,
                    is_final=False,
                )

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

        if not thinking_ended:
            self._safe_cb(cb and cb.on_thinking_end)

        state.tool_calls = [tc_acc[i] for i in sorted(tc_acc)]

    async def _handle_tool_calls_stream(
        self,
        tool_calls: list[dict],
        disabled_names: frozenset[str],
        *,
        in_reply_to: UUID,
    ) -> AsyncIterator[OutboundMessage]:
        """Persist tool round, yield TOOL_CALL / TOOL_RESULT outbounds, mirror non-stream path."""
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

            payload = json.dumps(
                {"id": tc["id"], "name": tool_name, "arguments": tc["arguments"]},
                ensure_ascii=False,
            )
            yield self._make_outbound(
                in_reply_to,
                content=payload,
                content_type=OutboundContentType.TOOL_CALL,
                is_final=False,
                metadata={"tool_name": tool_name},
            )

            self._safe_cb(cb and cb.on_tool_start, tool_name, args)
            logger.info("Calling tool: %s(%s)", tool_name, args)

            t0 = time.monotonic()
            ctx_rt = get_current_runtime()
            result_content, tr = await self._tool_runtime.execute(
                tool_name,
                args,
                ctx_rt,
                disabled_names=disabled_names,
            )
            duration = time.monotonic() - t0

            logger.info(
                "Tool %s → success=%s, content_len=%d",
                tool_name,
                tr.success,
                len(result_content),
            )
            self._safe_cb(cb and cb.on_tool_end, tool_name, args, result_content, duration)

            self._accumulate_knowledge_hits(tr)

            tool_msg = {
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result_content,
            }
            self.messages.append(tool_msg)
            self._persist_message(tool_msg)

            yield self._make_outbound(
                in_reply_to,
                content=result_content,
                content_type=OutboundContentType.TOOL_RESULT,
                is_final=False,
                metadata={
                    "tool_name": tool_name,
                    "success": tr.success,
                    "duration_s": duration,
                },
            )

    def _llm_call(
        self,
        system_prompt: str,
        *,
        openai_tools: list[dict],
    ) -> tuple[str, str | None, list[dict], tuple[int | None, int | None]]:
        """Unified LLM call — streaming when ``callbacks.on_text_chunk`` is set.

        Returns
        -------
        (content, finish_reason, tool_calls)
            ``tool_calls`` is a list of plain dicts with keys
            ``{id, name, arguments}`` ready for ``_handle_tool_calls_from_stream``.

        The fourth tuple element is ``(prompt_tokens, completion_tokens)`` from the
        API when available (streaming responses often omit usage).
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
                tools=cast(list[Any], openai_tools),
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                **self._chat_completion_extra(),
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
            usage = getattr(response, "usage", None)
            pt = getattr(usage, "prompt_tokens", None) if usage else None
            ct = getattr(usage, "completion_tokens", None) if usage else None
            return msg.content or "", choice.finish_reason, tool_calls_raw, (pt, ct)

        # ── Streaming path ────────────────────────────────────────────────
        stream = self._client.chat.completions.create(
            model=self._model,
            messages=api_messages,
            tools=cast(list[Any], openai_tools),
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            stream=True,
            **self._chat_completion_extra(),
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
        return "".join(content_parts), finish_reason, tool_calls_list, (None, None)

    def _persist_message(self, message: dict) -> None:
        """Persist a single OpenAI-compatible message (SQLite when store is configured)."""
        if self._context is None:
            return
        try:
            self._context.add_message(self.config.session_id, message)
        except SessionArchivedError:
            logger.error("Cannot persist to archived session %s", self.config.session_id)
        except OSError as exc:
            logger.error("Failed to persist message: %s", exc)

    async def _handle_tool_calls_from_stream(
        self,
        tool_calls: list[dict],
        disabled_names: frozenset[str],
    ) -> None:
        """Execute tool calls (side effects only); streaming yields are discarded."""
        async for _ in self._handle_tool_calls_stream(
            tool_calls,
            disabled_names,
            in_reply_to=uuid.uuid4(),
        ):
            pass
