"""RAG Service — FastAPI microservice exposing LightRAG to the TS Agent.

Endpoints:
  POST /rag/query                         — hybrid/course/personal/enrolled_courses retrieval
  POST /rag/generate-quiz                 — question generation from a course's knowledge graph
  POST /rag/build-mindmap                 — mindmap from parsed Markdown files
  POST /rag/eval                          — LLM-based evaluation (hint / score_essay / evaluate_code)
  POST /rag/parse-document                — base64 → extracted text (PDF / office / image)
  POST /rag/assignment/regenerate-question — regenerate a single assignment question via RAG
  POST /rag/assignment/complete-question  — complete a teacher-written question stem with AI

Auth: X-Internal-Key header must match RAG_SERVICE_API_KEY env var.
"""

from __future__ import annotations

import asyncio
import base64
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

import uvicorn
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, status
from loguru import logger
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Internal-key auth
# ---------------------------------------------------------------------------

_RAG_KEY = (os.environ.get("RAG_SERVICE_API_KEY") or "").strip()


def _require_key(request: Request) -> None:
    if not _RAG_KEY:
        return  # no key configured → allow all (dev mode)
    given = (request.headers.get("x-internal-key") or "").strip()
    if given != _RAG_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="RAG Service", version="1.0.0", docs_url="/docs")

# ---------------------------------------------------------------------------
# /rag/query
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    source: str  # "personal" | "course" | "all" | "enrolled_courses"
    user_id: str
    course_id: str | None = None
    accessible_course_ids: list[str] = Field(default_factory=list)
    question: str
    mode: str = "hybrid"
    top_k: int = Field(default=5, ge=1, le=20)


class HitItem(BaseModel):
    chunk_id: str
    text: str
    origin: str
    course_id: str | None = None
    material_id: str | None = None
    material_title: str | None = None
    relevance_score: float = 0.0
    image_urls: list[dict[str, Any]] = Field(default_factory=list)


class QueryResponse(BaseModel):
    hits: list[HitItem]
    warnings: list[str] = Field(default_factory=list)


def _fetch_chunk_page_mappings(chunk_ids: list[str]) -> dict[str, int]:
    """Fetch chunk_id → page_idx from chunk_page_mappings table."""
    if not chunk_ids:
        return {}
    try:
        from rag_mvp.db import connect_sync

        with connect_sync() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT chunk_id, page_idx FROM chunk_page_mappings WHERE chunk_id = ANY(%s)",
                    (chunk_ids,),
                )
                return {row[0]: row[1] for row in cur.fetchall()}
    except Exception as exc:  # noqa: BLE001
        logger.warning("chunk_page_mappings lookup failed: {}", exc)
        return {}


def _enrich_hits(
    raw_hits: list[dict[str, Any]],
    *,
    course_id: str | None,
    material_titles: dict[str, str],
    material_images: dict[str, list[dict[str, Any]]],
    chunk_page_mappings: dict[str, int],
) -> list[HitItem]:
    """Promote nested metadata fields and attach material_title / image_urls from DB lookup."""
    items: list[HitItem] = []
    for h in raw_hits:
        meta = h.get("metadata") or {}
        mid = meta.get("material_id") if isinstance(meta, dict) else None
        chunk_id = str(h.get("chunk_id") or "")
        page_idx = chunk_page_mappings.get(chunk_id)
        if page_idx is not None and mid:
            all_images = material_images.get(mid, [])
            image_urls = [img for img in all_images if img.get("page_idx") == page_idx]
        else:
            image_urls = []
        items.append(
            HitItem(
                chunk_id=chunk_id,
                text=str(h.get("text") or ""),
                origin=str(h.get("origin") or "unknown"),
                course_id=course_id,
                material_id=mid,
                material_title=material_titles.get(mid) if mid else None,
                relevance_score=float(h.get("relevance_score") or 0.0),
                image_urls=image_urls,
            )
        )
    return items


def _fetch_material_titles(material_ids: list[str]) -> dict[str, str]:
    """Fetch material filename/title from PostgreSQL for a list of material IDs."""
    if not material_ids:
        return {}
    try:
        from rag_mvp.db import connect_sync

        with connect_sync() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id::text, original_filename
                    FROM materials
                    WHERE id = ANY(%s::uuid[]) AND NOT is_deleted
                    """,
                    (material_ids,),
                )
                return {row[0]: row[1] for row in cur.fetchall()}
    except Exception as exc:  # noqa: BLE001
        logger.warning("material_titles lookup failed: {}", exc)
        return {}


def _fetch_material_images(material_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Fetch image records from material_images table, keyed by material_id."""
    if not material_ids:
        return {}
    try:
        from rag_mvp.db import connect_sync

        with connect_sync() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT material_id::text, page_idx, minio_url
                    FROM material_images
                    WHERE material_id = ANY(%s::uuid[])
                    ORDER BY material_id, page_idx
                    """,
                    (material_ids,),
                )
                result: dict[str, list[dict[str, Any]]] = {}
                for mid, page_idx, minio_url in cur.fetchall():
                    result.setdefault(mid, []).append({"page_idx": page_idx, "url": minio_url})
                return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("material_images lookup failed: {}", exc)
        return {}


@app.post("/rag/query", response_model=QueryResponse)
def rag_query(body: QueryRequest, _auth: None = Depends(_require_key)) -> QueryResponse:
    from rag_mvp.engine import (
        course_retrieval_hits_sync,
        personal_retrieval_hits_sync,
    )

    source = body.source.strip().lower()
    mode = body.mode or "hybrid"
    top_k = body.top_k
    question = body.question.strip()
    warnings: list[str] = []

    if not question:
        raise HTTPException(status_code=400, detail="question must not be empty")

    # Hard guard: single-course sessions must not fan out to enrolled courses;
    # hub sessions must not query course-scoped sources without an active course.
    has_course_context = bool((body.course_id or "").strip())
    if has_course_context and source == "enrolled_courses":
        raise HTTPException(
            status_code=400,
            detail="source=enrolled_courses is not allowed when course_id is present",
        )
    if (not has_course_context) and source in ("course", "all"):
        raise HTTPException(
            status_code=400,
            detail="source=course/all requires course_id",
        )

    raw_hits: list[dict[str, Any]] = []

    if source == "personal":
        raw_hits = personal_retrieval_hits_sync(body.user_id, question, mode=mode, top_k=top_k)

    elif source == "course":
        if not body.course_id:
            raise HTTPException(status_code=400, detail="course_id required for source=course")
        raw_hits = course_retrieval_hits_sync(
            body.course_id, question, mode=mode, top_k=top_k
        )

    elif source == "all":
        # personal + current course merged
        personal = personal_retrieval_hits_sync(body.user_id, question, mode=mode, top_k=top_k)
        course_hits: list[dict[str, Any]] = []
        if body.course_id:
            course_hits = course_retrieval_hits_sync(
                body.course_id, question, mode=mode, top_k=top_k
            )
        raw_hits = course_hits + personal

    elif source == "enrolled_courses":
        # Query each accessible course, merge and deduplicate by chunk_id
        seen: set[str] = set()
        for cid in (body.accessible_course_ids or []):
            try:
                hits = course_retrieval_hits_sync(cid, question, mode=mode, top_k=top_k)
                for h in hits:
                    cid_chunk = str(h.get("chunk_id") or "")
                    if cid_chunk and cid_chunk in seen:
                        continue
                    seen.add(cid_chunk)
                    # Tag with originating course_id for downstream enrichment
                    h["_course_id"] = cid
                    raw_hits.append(h)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"course {cid}: {exc}")

        # Sort merged hits by relevance_score descending, keep top_k
        raw_hits.sort(key=lambda h: float(h.get("relevance_score") or 0.0), reverse=True)
        raw_hits = raw_hits[:top_k]

    else:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid source={source!r}. Allowed: personal, course, all, enrolled_courses",
        )

    # Collect material IDs for title and image lookup
    material_ids = list(
        {
            str(h.get("metadata", {}).get("material_id") or "")
            for h in raw_hits
            if isinstance(h.get("metadata"), dict) and h["metadata"].get("material_id")
        }
    )
    chunk_ids = [str(h.get("chunk_id") or "") for h in raw_hits if h.get("chunk_id")]
    material_titles = _fetch_material_titles(material_ids)
    material_images = _fetch_material_images(material_ids)
    chunk_page_mappings = _fetch_chunk_page_mappings(chunk_ids)

    # For enrolled_courses, use per-hit _course_id; otherwise use body.course_id
    if source == "enrolled_courses":
        items: list[HitItem] = []
        for h in raw_hits:
            cid = h.pop("_course_id", None)
            meta = h.get("metadata") or {}
            mid = meta.get("material_id") if isinstance(meta, dict) else None
            chunk_id = str(h.get("chunk_id") or "")
            page_idx = chunk_page_mappings.get(chunk_id)
            if page_idx is not None and mid:
                all_images = material_images.get(mid, [])
                image_urls = [img for img in all_images if img.get("page_idx") == page_idx]
            else:
                image_urls = []
            items.append(
                HitItem(
                    chunk_id=chunk_id,
                    text=str(h.get("text") or ""),
                    origin=str(h.get("origin") or "course"),
                    course_id=cid,
                    material_id=mid,
                    material_title=material_titles.get(mid) if mid else None,
                    relevance_score=float(h.get("relevance_score") or 0.0),
                    image_urls=image_urls,
                )
            )
        return QueryResponse(hits=items, warnings=warnings)

    course_id_for_hits = body.course_id if source in ("course", "all") else None
    hits = _enrich_hits(
        raw_hits,
        course_id=course_id_for_hits,
        material_titles=material_titles,
        material_images=material_images,
        chunk_page_mappings=chunk_page_mappings,
    )
    return QueryResponse(hits=hits, warnings=warnings)


# ---------------------------------------------------------------------------
# /rag/generate-quiz
# ---------------------------------------------------------------------------

class GenerateQuizRequest(BaseModel):
    course_id: str
    count: int = Field(default=5, ge=1, le=20)
    question_type: str = "mixed"  # single_choice|multi_choice|fill_blank|short_answer|mixed


class GenerateQuizResponse(BaseModel):
    questions: list[dict[str, Any]]
    total: int = 0
    generated_at: str = ""


_quiz_lock = threading.Lock()


@app.post("/rag/generate-quiz", response_model=GenerateQuizResponse)
def rag_generate_quiz(
    body: GenerateQuizRequest, _auth: None = Depends(_require_key)
) -> GenerateQuizResponse:
    from rag_mvp.question_gen import (
        DEFAULT_TYPE_WEIGHTS,
        generate,
    )
    from rag_mvp.course_workspace import course_id_to_workspace
    from rag_mvp.config import settings

    # Resolve course working_dir so question_gen reads the right graphml
    ws = course_id_to_workspace(body.course_id)
    rag_storage_root = getattr(settings, "rag_storage_dir", None) or Path("rag_storage")
    course_working_dir = Path(rag_storage_root) / ws

    if not course_working_dir.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Course knowledge base not found for course_id={body.course_id!r}",
        )

    # Build type_weights from question_type
    if body.question_type in DEFAULT_TYPE_WEIGHTS:
        type_weights = {body.question_type: 1.0}
    else:
        type_weights = None  # "mixed" → use defaults

    # question_gen.generate() reads from settings.working_dir — swap with lock for thread safety
    with _quiz_lock:
        original_wd = settings.working_dir
        settings.working_dir = course_working_dir
        try:
            result = generate(count=body.count, type_weights=type_weights)
        finally:
            settings.working_dir = original_wd

    return GenerateQuizResponse(
        questions=result.get("questions") or [],
        total=result.get("total") or 0,
        generated_at=str(result.get("generated_at") or ""),
    )


# ---------------------------------------------------------------------------
# /rag/build-mindmap
# ---------------------------------------------------------------------------

class BuildMindmapRequest(BaseModel):
    source: str
    refine: bool = False


class BuildMindmapResponse(BaseModel):
    markdown: str
    html: str


@app.post("/rag/build-mindmap", response_model=BuildMindmapResponse)
def rag_build_mindmap(
    body: BuildMindmapRequest, _auth: None = Depends(_require_key)
) -> BuildMindmapResponse:
    from rag_mvp.mindmap import build_structure_mindmap, build_llm_mindmap

    build_fn = build_llm_mindmap if body.refine else build_structure_mindmap
    try:
        html_paths = build_fn(body.source)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if not html_paths:
        raise HTTPException(status_code=404, detail="No output generated for source")

    html_path = html_paths[0]
    md_path = html_path.with_suffix(".md")

    html = html_path.read_text(encoding="utf-8") if html_path.exists() else ""
    markdown = md_path.read_text(encoding="utf-8") if md_path.exists() else ""

    return BuildMindmapResponse(markdown=markdown, html=html)


# ---------------------------------------------------------------------------
# /rag/eval
# ---------------------------------------------------------------------------

class EvalRequest(BaseModel):
    eval_type: str  # "hint" | "score_essay" | "evaluate_code"
    # hint params
    question: str | None = None
    context: str | None = None
    level: int = 1
    # score_essay params
    answer: str | None = None
    reference: str | None = None
    # evaluate_code params
    code: str | None = None
    task_description: str | None = None
    language: str = "python"


class EvalResponse(BaseModel):
    result: str


_HINT_SYSTEM = """你是一位启发式教学助手，专注于苏格拉底式提问。
你的职责是通过分级提示引导学生自己找到答案，而不是直接给出答案。
提示等级：1=轻微方向性引导，2=提供部分思路，3=接近答案但不揭示。"""

_ESSAY_SYSTEM = """你是一位专业教育评估者，负责批改学生书面作答。
请基于评分标准给出：① 分数（0-100）② 优点 ③ 不足 ④ 改进建议。输出格式为 Markdown。"""

_CODE_SYSTEM = """你是一位代码审查专家，专门对学生代码提供教育性反馈。
请评估：① 正确性 ② 代码质量 ③ 边界情况 ④ 改进建议。输出格式为 Markdown。"""


async def _llm_eval(system: str, user: str) -> str:
    from openai import AsyncOpenAI
    from rag_mvp.config import settings

    client = AsyncOpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)
    resp = await client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.3,
        max_tokens=2048,
    )
    return resp.choices[0].message.content or ""


@app.post("/rag/eval", response_model=EvalResponse)
def rag_eval(body: EvalRequest, _auth: None = Depends(_require_key)) -> EvalResponse:
    eval_type = (body.eval_type or "").strip().lower()

    if eval_type == "hint":
        q = (body.question or "").strip()
        if not q:
            raise HTTPException(status_code=400, detail="question required for hint eval")
        lvl = max(1, min(3, body.level))
        user = f"问题：{q}\n\n"
        if body.context:
            user += f"背景信息：{body.context}\n\n"
        user += f"请提供第 {lvl} 级提示（1=最轻微，3=最接近答案）。"
        result = asyncio.run(_llm_eval(_HINT_SYSTEM, user))

    elif eval_type == "score_essay":
        q = (body.question or "").strip()
        ans = (body.answer or "").strip()
        if not q or not ans:
            raise HTTPException(status_code=400, detail="question and answer required for score_essay")
        user = f"题目：{q}\n\n学生作答：{ans}"
        if body.reference:
            user += f"\n\n评分标准：{body.reference}"
        result = asyncio.run(_llm_eval(_ESSAY_SYSTEM, user))

    elif eval_type == "evaluate_code":
        code = (body.code or "").strip()
        task = (body.task_description or "").strip()
        if not code or not task:
            raise HTTPException(status_code=400, detail="code and task_description required for evaluate_code")
        user = f"编程语言：{body.language}\n\n任务要求：{task}\n\n学生代码：\n```{body.language}\n{code}\n```"
        result = asyncio.run(_llm_eval(_CODE_SYSTEM, user))

    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown eval_type={eval_type!r}. Allowed: hint, score_essay, evaluate_code",
        )

    return EvalResponse(result=result)


# ---------------------------------------------------------------------------
# /rag/parse-document
# ---------------------------------------------------------------------------

class ParseDocumentRequest(BaseModel):
    filename: str = "document.pdf"
    base64_content: str


class ParseDocumentResponse(BaseModel):
    text: str
    pages: int | None = None


def _extract_text_pypdf(data: bytes) -> tuple[str, int]:
    """Fast text extraction from PDF using pypdf (no MinerU required)."""
    from pypdf import PdfReader
    import io

    reader = PdfReader(io.BytesIO(data))
    pages = len(reader.pages)
    parts: list[str] = []
    for page in reader.pages:
        t = page.extract_text() or ""
        if t.strip():
            parts.append(t)
    return "\n\n".join(parts), pages


def _extract_text_plaintext(data: bytes, encoding: str = "utf-8") -> tuple[str, None]:
    try:
        return data.decode(encoding, errors="replace"), None
    except Exception:
        return data.decode("latin-1", errors="replace"), None


@app.post("/rag/parse-document", response_model=ParseDocumentResponse)
def rag_parse_document(
    body: ParseDocumentRequest, _auth: None = Depends(_require_key)
) -> ParseDocumentResponse:
    try:
        raw = base64.b64decode(body.base64_content)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid base64_content: {exc}") from exc

    filename = Path(body.filename)
    suffix = filename.suffix.lower()

    # --- PDF ---
    if suffix == ".pdf":
        try:
            text, pages = _extract_text_pypdf(raw)
            return ParseDocumentResponse(text=text, pages=pages)
        except Exception as exc:
            logger.warning("pypdf failed for {}: {}", filename, exc)
            raise HTTPException(status_code=422, detail=f"PDF parse failed: {exc}") from exc

    # --- Plain text / Markdown ---
    if suffix in (".txt", ".md"):
        text, _ = _extract_text_plaintext(raw)
        return ParseDocumentResponse(text=text, pages=None)

    # --- Office / image: try MinerU via temp file ---
    _MINERU_SUFFIXES = frozenset({".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".jpg", ".jpeg", ".png"})
    if suffix in _MINERU_SUFFIXES:
        try:
            from rag_mvp.engine import parse_file

            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(raw)
                tmp_path = Path(tmp.name)
            try:
                parse_file(tmp_path)
                # Collect markdown output
                out_dir = Path("output") / "parsed" / tmp_path.stem
                md_texts: list[str] = []
                if out_dir.exists():
                    for md in sorted(out_dir.rglob("*.md")):
                        if md.stat().st_size > 100:
                            md_texts.append(md.read_text(encoding="utf-8", errors="replace"))
                text = "\n\n".join(md_texts)
                return ParseDocumentResponse(text=text, pages=None)
            finally:
                tmp_path.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("MinerU parse failed for {}: {}", filename, exc)
            raise HTTPException(status_code=422, detail=f"Document parse failed: {exc}") from exc

    raise HTTPException(status_code=415, detail=f"Unsupported file type: {suffix}")


# ---------------------------------------------------------------------------
# /rag/assignment/regenerate-question  &  /rag/assignment/complete-question
# ---------------------------------------------------------------------------


class RegenerateQuestionRequest(BaseModel):
    course_id: str
    entity_names: list[str] = Field(default_factory=list)
    q_type: str = "single_choice"
    objective: str = "knowledge"
    q_id: int = 1
    extra_requirements: str = ""
    current_question: str = ""
    difficulty: str = "medium"


class CompleteQuestionRequest(BaseModel):
    course_id: str
    entity_names: list[str] = Field(default_factory=list)
    question_stem: str
    answer_hint: str = ""
    q_type: str = "single_choice"
    objective: str = "knowledge"
    q_id: int = 1
    difficulty: str = "medium"


@app.post("/rag/assignment/regenerate-question")
async def rag_regenerate_question(
    body: RegenerateQuestionRequest,
    _auth: None = Depends(_require_key),
) -> dict[str, Any]:
    from rag_mvp.assignment_gen import regenerate_one_question

    result = await regenerate_one_question(
        course_id=body.course_id,
        entity_names=body.entity_names,
        q_type=body.q_type,
        objective=body.objective,
        q_id=body.q_id,
        extra_requirements=body.extra_requirements,
        current_question=body.current_question,
        difficulty=body.difficulty,
    )
    if result is None:
        raise HTTPException(status_code=502, detail="Question generation failed — check RAG logs")
    return result


@app.post("/rag/assignment/complete-question")
async def rag_complete_question(
    body: CompleteQuestionRequest,
    _auth: None = Depends(_require_key),
) -> dict[str, Any]:
    from rag_mvp.assignment_gen import complete_teacher_question

    result = await complete_teacher_question(
        course_id=body.course_id,
        entity_names=body.entity_names,
        question_stem=body.question_stem,
        answer_hint=body.answer_hint,
        q_type=body.q_type,
        objective=body.objective,
        q_id=body.q_id,
        difficulty=body.difficulty,
    )
    if result is None:
        raise HTTPException(status_code=502, detail="Question completion failed — check RAG logs")
    return result


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Entry point (pyproject.toml: rag-service = "rag_service.main:run")
# ---------------------------------------------------------------------------

def run() -> None:
    # Load .env files before reading any config — same order as worker.py.
    # System env vars already set always win (load_dotenv default: override=False).
    _here = os.path.dirname(os.path.abspath(__file__))
    _root = os.path.abspath(os.path.join(_here, "..", ".."))
    load_dotenv(os.path.join(_root, ".env"))
    load_dotenv(os.path.join(_root, "edu-platform", ".env"))

    host = os.environ.get("RAG_SERVICE_HOST", "0.0.0.0")
    port = int(os.environ.get("RAG_SERVICE_PORT", "8001"))
    logger.info("Starting RAG Service on {}:{}", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")
