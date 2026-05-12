"""MinerU Cloud API client — 精准解析 v4 (mineru.net).

Flow for a single file
─────────────────────
1. POST /api/v4/file-urls/batch  → batch_id + pre-signed upload URL
2. PUT <upload_url>              → upload raw bytes (no Content-Type header)
3. Poll GET /api/v4/extract-results/batch/{batch_id}
       until state == "done" | "failed" or timeout
4. Download full_zip_url → bytes
5. Unzip into out_dir  (same layout as local MinerU output)

The caller then reads *_content_list.json from out_dir, runs _fix_image_paths,
and passes the result to insert_content_list — exactly the same as reindex_from_cache.
"""

from __future__ import annotations

import asyncio
import shutil
import io
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from pypdf import PdfReader, PdfWriter
from loguru import logger

from .config import settings

_BASE_URL = "https://mineru.net/api/v4"
_BATCH_URL = f"{_BASE_URL}/file-urls/batch"
_RESULTS_URL = f"{_BASE_URL}/extract-results/batch"
_MAX_FILE_BYTES = 200 * 1024 * 1024
_MAX_FILE_PAGES = 200
_MAX_BATCH_FILES = 200


class MineruCloudError(RuntimeError):
    """Raised when the MinerU Cloud API returns an error or times out."""


@dataclass(frozen=True)
class _UploadPart:
    source_path: Path
    upload_name: str
    output_subdir: str
    page_from: int | None = None
    page_to: int | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.mineru_cloud_api_key}",
        "Content-Type": "application/json",
    }


def _pdf_page_count(file_path: Path) -> int:
    reader = PdfReader(str(file_path))
    return len(reader.pages)


def _write_pdf_slice(reader: PdfReader, out_path: Path, start: int, end: int) -> None:
    writer = PdfWriter()
    for i in range(start, end):
        writer.add_page(reader.pages[i])
    with out_path.open("wb") as f:
        writer.write(f)


def _prepare_upload_parts(file_path: Path, temp_dir: Path) -> list[_UploadPart]:
    """Create upload parts respecting MinerU cloud limits.

    Strategy:
    - For files within limits, upload as-is.
    - For oversized PDFs, split into chunks where each part satisfies
      page-count <= 200 and file-size <= 200MB.
    - For non-PDF files over 200MB, raise and let caller decide fallback.
    """
    size_ok = file_path.stat().st_size <= _MAX_FILE_BYTES
    suffix = file_path.suffix.lower()

    # Fast path: non-PDF or already within limits.
    if suffix != ".pdf":
        if not size_ok:
            raise MineruCloudError(
                "非 PDF 文件超过 MinerU 云端 200MB 限制，无法自动切分；请改用本地解析或手工拆分"
            )
        return [
            _UploadPart(
                source_path=file_path,
                upload_name=file_path.name,
                output_subdir="part_0001",
            ),
        ]

    total_pages = _pdf_page_count(file_path)
    if size_ok and total_pages <= _MAX_FILE_PAGES:
        return [
            _UploadPart(
                source_path=file_path,
                upload_name=file_path.name,
                output_subdir="part_0001",
                page_from=1,
                page_to=total_pages,
            ),
        ]

    reader = PdfReader(str(file_path))
    parts: list[_UploadPart] = []
    start = 0
    part_idx = 1

    while start < total_pages:
        # Start with page-window limit first.
        candidate_end = min(start + _MAX_FILE_PAGES, total_pages)

        # Shrink by size if needed.
        while True:
            part_name = f"{file_path.stem}__part_{part_idx:04d}.pdf"
            part_path = temp_dir / part_name
            _write_pdf_slice(reader, part_path, start, candidate_end)
            if part_path.stat().st_size <= _MAX_FILE_BYTES:
                break

            if candidate_end - start <= 1:
                raise MineruCloudError(
                    f"PDF 第 {start + 1} 页单页已超过 200MB，无法自动切分上传: {file_path.name}"
                )

            # Reduce chunk aggressively to converge faster.
            span = candidate_end - start
            candidate_end = start + max(1, span // 2)

        parts.append(
            _UploadPart(
                source_path=part_path,
                upload_name=part_name,
                output_subdir=f"part_{part_idx:04d}",
                page_from=start + 1,
                page_to=candidate_end,
            ),
        )
        start = candidate_end
        part_idx += 1

    return parts


async def _request_upload_urls(
    client: httpx.AsyncClient,
    parts: list[_UploadPart],
) -> tuple[str, list[str]]:
    """Step 1: obtain batch_id and pre-signed upload URLs."""
    payload: dict[str, Any] = {
        "files": [{"name": p.upload_name} for p in parts],
        "model_version": settings.mineru_cloud_model_version,
        "enable_formula": True,
        "enable_table": True,
        "language": settings.mineru_lang,
    }
    resp = await client.post(_BATCH_URL, json=payload, headers=_auth_headers())
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") != 0:
        raise MineruCloudError(
            f"申请上传链接失败: code={body.get('code')} msg={body.get('msg')}"
        )
    batch_id: str = body["data"]["batch_id"]
    file_urls: list[str] = body["data"].get("file_urls") or []
    if len(file_urls) != len(parts):
        raise MineruCloudError(
            f"上传链接数量不匹配: expected={len(parts)} actual={len(file_urls)}"
        )
    return batch_id, file_urls


async def _upload_file(
    client: httpx.AsyncClient,
    upload_url: str,
    file_path: Path,
) -> None:
    """Step 2: PUT raw file bytes to pre-signed OSS URL (no extra headers)."""
    data = file_path.read_bytes()
    # Strip Content-Type — OSS pre-signed URL must not have it
    resp = await client.put(upload_url, content=data, headers={})
    if resp.status_code not in (200, 201):
        raise MineruCloudError(
            f"文件上传失败: HTTP {resp.status_code} {resp.text[:200]}"
        )


async def _upload_files(
    client: httpx.AsyncClient,
    parts: list[_UploadPart],
    upload_urls: list[str],
) -> None:
    for part, url in zip(parts, upload_urls):
        logger.info(
            "MinerU Cloud: 上传分片 {} ({} MB)...",
            part.upload_name,
            f"{part.source_path.stat().st_size / 1024 / 1024:.2f}",
        )
        await _upload_file(client, url, part.source_path)


async def _poll_until_done(
    client: httpx.AsyncClient,
    batch_id: str,
) -> dict[str, dict[str, Any]]:
    """Step 3: poll batch results until all done/failed, return result map by file_name."""
    url = f"{_RESULTS_URL}/{batch_id}"
    deadline = asyncio.get_running_loop().time() + settings.mineru_cloud_timeout
    _STATE_LABELS = {
        "waiting-file": "等待文件确认",
        "pending": "排队中",
        "running": "解析中",
        "converting": "格式转换中",
    }
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise MineruCloudError(
                f"云端解析超时 ({settings.mineru_cloud_timeout}s), batch_id={batch_id}"
            )

        resp = await client.get(url, headers=_auth_headers())
        resp.raise_for_status()
        body = resp.json()
        if body.get("code") != 0:
            raise MineruCloudError(
                f"查询任务结果失败: code={body.get('code')} msg={body.get('msg')}"
            )

        results: list[dict[str, Any]] = body["data"].get("extract_result") or []
        if not results:
            await asyncio.sleep(settings.mineru_cloud_poll_interval)
            continue

        states = [str(r.get("state", "")) for r in results]
        if any(s == "failed" for s in states):
            failed_entry = next(r for r in results if r.get("state") == "failed")
            raise MineruCloudError(
                f"云端解析失败: {failed_entry.get('file_name')} - {failed_entry.get('err_msg') or '未知错误'}"
            )

        if all(s == "done" for s in states):
            by_name: dict[str, dict[str, Any]] = {}
            for entry in results:
                name = str(entry.get("file_name") or "")
                if not name:
                    continue
                by_name[name] = entry
            return by_name

        summary: dict[str, int] = {}
        for s in states:
            key = _STATE_LABELS.get(s, s)
            summary[key] = summary.get(key, 0) + 1
        logger.debug("MinerU Cloud [{}] 状态: {}", batch_id[:8], summary)
        await asyncio.sleep(settings.mineru_cloud_poll_interval)


async def _download_and_extract(
    client: httpx.AsyncClient,
    zip_url: str,
    out_dir: Path,
) -> None:
    """Step 4-5: download ZIP and extract to out_dir."""
    resp = await client.get(zip_url, follow_redirects=True)
    resp.raise_for_status()
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        zf.extractall(out_dir)
    logger.debug("MinerU Cloud ZIP extracted to {}", out_dir)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def parse_file_via_cloud(file_path: Path, out_dir: Path) -> None:
    """Parse *file_path* via MinerU Cloud API and unzip results into *out_dir*.

    Raises:
        MineruCloudError: on any API error or timeout.
    """
    if not settings.mineru_cloud_api_key:
        raise MineruCloudError("MINERU_CLOUD_API_KEY 未配置")

    # Avoid stale parse artifacts mixing with fresh cloud outputs.
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    logger.info("MinerU Cloud: 准备解析 {}", file_path.name)

    # httpx timeout: connect 30s, read/write 120s (file upload may be slow)
    timeout = httpx.Timeout(connect=30.0, read=120.0, write=120.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        with tempfile.TemporaryDirectory(prefix="mineru_cloud_parts_") as tmp:
            parts = _prepare_upload_parts(file_path, Path(tmp))
            logger.info("MinerU Cloud: 共 {} 个分片待上传", len(parts))

            for i in range(0, len(parts), _MAX_BATCH_FILES):
                batch_parts = parts[i:i + _MAX_BATCH_FILES]
                batch_no = (i // _MAX_BATCH_FILES) + 1
                logger.info(
                    "MinerU Cloud: 提交批次 {}/{} ({} 个文件)",
                    batch_no,
                    (len(parts) - 1) // _MAX_BATCH_FILES + 1,
                    len(batch_parts),
                )
                batch_id, upload_urls = await _request_upload_urls(client, batch_parts)
                await _upload_files(client, batch_parts, upload_urls)

                results_by_name = await _poll_until_done(client, batch_id)

                for part in batch_parts:
                    entry = results_by_name.get(part.upload_name)
                    if entry is None:
                        raise MineruCloudError(
                            f"批次结果缺少分片记录: {part.upload_name}"
                        )
                    zip_url = str(entry.get("full_zip_url") or "")
                    if not zip_url:
                        raise MineruCloudError(
                            f"分片解析完成但 full_zip_url 为空: {part.upload_name}"
                        )

                    target_dir = out_dir / "cloud_parts" / part.output_subdir
                    await _download_and_extract(client, zip_url, target_dir)

    logger.success("MinerU Cloud: {} → {}", file_path.name, out_dir)
