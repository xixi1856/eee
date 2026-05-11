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
import io
import zipfile
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from .config import settings

_BASE_URL = "https://mineru.net/api/v4"
_BATCH_URL = f"{_BASE_URL}/file-urls/batch"
_RESULTS_URL = f"{_BASE_URL}/extract-results/batch"


class MineruCloudError(RuntimeError):
    """Raised when the MinerU Cloud API returns an error or times out."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.mineru_cloud_api_key}",
        "Content-Type": "application/json",
    }


async def _request_upload_url(
    client: httpx.AsyncClient,
    file_name: str,
) -> tuple[str, str]:
    """Step 1: obtain batch_id and pre-signed upload URL."""
    payload: dict[str, Any] = {
        "files": [{"name": file_name}],
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
    file_url: str = body["data"]["file_urls"][0]
    return batch_id, file_url


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


async def _poll_until_done(
    client: httpx.AsyncClient,
    batch_id: str,
    file_name: str,
) -> str:
    """Step 3: poll batch results until done/failed, return full_zip_url."""
    url = f"{_RESULTS_URL}/{batch_id}"
    deadline = asyncio.get_event_loop().time() + settings.mineru_cloud_timeout
    _STATE_LABELS = {
        "waiting-file": "等待文件确认",
        "pending": "排队中",
        "running": "解析中",
        "converting": "格式转换中",
    }
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
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

        results: list[dict] = body["data"].get("extract_result") or []
        # Match by file_name (batch may contain only one file in our usage)
        entry = next((r for r in results if r.get("file_name") == file_name), None)
        if entry is None and results:
            entry = results[0]  # fallback: single-file batch

        if entry is None:
            await asyncio.sleep(settings.mineru_cloud_poll_interval)
            continue

        state: str = entry.get("state", "")
        if state == "done":
            zip_url: str = entry.get("full_zip_url", "")
            if not zip_url:
                raise MineruCloudError("任务完成但 full_zip_url 为空")
            return zip_url

        if state == "failed":
            raise MineruCloudError(
                f"云端解析失败: {entry.get('err_msg') or '未知错误'}"
            )

        label = _STATE_LABELS.get(state, state)
        logger.debug("MinerU Cloud [{}] {}", batch_id[:8], label)
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

    file_name = file_path.name
    logger.info("MinerU Cloud: 上传 {} ...", file_name)

    # httpx timeout: connect 30s, read/write 120s (file upload may be slow)
    timeout = httpx.Timeout(connect=30.0, read=120.0, write=120.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        batch_id, upload_url = await _request_upload_url(client, file_name)
        logger.info("MinerU Cloud: batch_id={} 开始上传...", batch_id[:8])

        await _upload_file(client, upload_url, file_path)
        logger.info("MinerU Cloud: 上传完成，等待解析...")

        zip_url = await _poll_until_done(client, batch_id, file_name)
        logger.info("MinerU Cloud: 解析完成，下载 ZIP...")

        await _download_and_extract(client, zip_url, out_dir)

    logger.success("MinerU Cloud: {} → {}", file_name, out_dir)
