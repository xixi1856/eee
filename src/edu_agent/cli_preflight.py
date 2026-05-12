"""Startup checks for ``edu chat`` — warnings only; do not block the session."""

from __future__ import annotations

import logging
import sys

import click
import httpx

from edu_agent.auth import token_store
from edu_agent.auth.checker import AuthorizationChecker, AuthorizationError
from edu_agent.config import EduSettings
from edu_agent.tools.rag import _resolve_platform_rag_base_key

logger = logging.getLogger(__name__)


def _warn(msg: str) -> None:
    logger.warning(msg)
    click.echo(click.style(f"[警告] {msg}", fg="yellow"), err=True)


def emit_edu_chat_startup_warnings(settings: EduSettings, *, user_id: str) -> None:
    """Emit stderr warnings before the first user prompt (binding + platform RAG config)."""
    identity = token_store.load()
    if identity is None or not (identity.get("channel_token") or "").strip():
        _warn(
            "未检测到教育平台绑定（~/.edu_agent/identity.json 无 channel_token）。"
            "knowledge_query 的 enrolled_courses 等依赖平台账号的能力不可用；普通对话仍可进行。"
            "需要时请执行：edu bind"
        )
    else:
        try:
            AuthorizationChecker.validate_channel_token(str(identity["channel_token"]))
        except AuthorizationError as exc:
            code = str(exc)
            if code == "channel_token_expired":
                _warn(
                    "渠道令牌已过期，课程侧能力可能失败；请执行 edu bind 重新绑定。"
                    "（启动时不会自动刷新令牌。）"
                )
            else:
                _warn(
                    f"渠道令牌无效（{code}），课程侧能力可能失败；请执行 edu bind。"
                )

    base, key = _resolve_platform_rag_base_key(str(settings.platform_base_url or ""))
    if not base:
        _warn(
            "未配置可访问的教育平台地址：请设置环境变量 EDU_PLATFORM_BASE_URL "
            "或在 edu_agent.yaml 中配置 platform_base_url，否则课程 knowledge_query 不可用。"
        )
        return
    if len(key) < 16:
        _warn(
            "EDU_PLATFORM_INTERNAL_API_KEY 未设置或长度不足 16，"
            "课程 knowledge_query 将不可用；请与 edu-platform 的 INTERNAL_API_KEY 保持一致。"
        )
        return

    if not sys.stdin.isatty():
        return

    uid = (user_id or "").strip() or "default"
    url = f"{base}/api/v1/internal/enrolled-courses-rag"
    try:
        with httpx.Client(timeout=2.0) as client:
            try:
                r = client.get(url, params={"user_id": uid}, headers={"X-Internal-Key": key})
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                _warn(
                    f"无法连接教育平台 ({base})，请确认已启动 npm run dev 或部署正在监听该地址: {exc}"
                )
                return
            if r.status_code >= 400:
                _warn(
                    f"教育平台内部接口返回 HTTP {r.status_code}（{base}），"
                    "请核对 EDU_PLATFORM_INTERNAL_API_KEY 是否与平台 INTERNAL_API_KEY 一致。"
                )
    except httpx.RequestError as exc:
        _warn(f"探测教育平台连接时出错: {exc}")
