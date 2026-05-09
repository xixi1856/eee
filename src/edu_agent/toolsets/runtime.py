"""Single async execution path for all tools (A4)."""

from __future__ import annotations

import asyncio
import json
import logging
import traceback
from typing import Any

import jsonschema
from jsonschema import ValidationError

from edu_agent.config import EduSettings, ToolsetsSettings
from edu_agent.runtime_context import TurnRuntimeContext
from edu_agent.toolsets.models import ToolSpec
from edu_agent.toolsets.permissions import PermissionChecker
from edu_agent.toolsets.registry import ToolsetRegistry
from edu_agent.toolsets.result_formatter import format_tool_result_for_model
from edu_agent.types import ToolResult

logger = logging.getLogger(__name__)

_RETRYABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    TimeoutError,
    asyncio.TimeoutError,
    ConnectionError,
    OSError,
)


class ToolRuntime:
    """Agent → ToolRuntime → Registry → PermissionChecker → Handler."""

    def __init__(
        self,
        registry: ToolsetRegistry,
        settings: EduSettings,
        permissions: PermissionChecker,
        *,
        max_retries: int = 3,
        backoff_base: float = 1.5,
    ) -> None:
        self._registry = registry
        self._settings = settings
        self._permissions = permissions
        self._max_retries = max_retries
        self._backoff_base = backoff_base

    @property
    def registry(self) -> ToolsetRegistry:
        return self._registry

    @property
    def permission_checker(self) -> PermissionChecker:
        return self._permissions

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: TurnRuntimeContext,
        *,
        disabled_names: frozenset[str] | None = None,
        allowed_names: frozenset[str] | None = None,
    ) -> tuple[str, ToolResult]:
        """Validate, permission-check, run handler with timeout/retry; return (content, ToolResult)."""
        _ = context  # reserved for future injection into handlers
        disabled_names = disabled_names or frozenset()
        if allowed_names is not None and tool_name not in allowed_names:
            err = f"工具不在白名单: {tool_name}"
            tr = ToolResult(tool_name=tool_name, success=False, summary="", error=err)
            return json.dumps({"error": err}, ensure_ascii=False), tr
        if tool_name in disabled_names:
            err = f"工具已禁用: {tool_name}"
            tr = ToolResult(tool_name=tool_name, success=False, summary="", error=err)
            return json.dumps({"error": err}, ensure_ascii=False), tr

        spec = self._registry.get_spec(tool_name)
        if spec is None:
            err = f"未知工具：{tool_name}"
            tr = ToolResult(tool_name=tool_name, success=False, summary="", error=err)
            return json.dumps({"error": err}, ensure_ascii=False), tr

        ts_cfg = self._settings.toolsets
        if not isinstance(ts_cfg, ToolsetsSettings):
            ts_cfg = ToolsetsSettings()
        if not ts_cfg.is_toolset_enabled(spec.toolset):
            err = f"工具集已禁用: {spec.toolset}"
            tr = ToolResult(tool_name=tool_name, success=False, summary="", error=err)
            return json.dumps({"error": err}, ensure_ascii=False), tr

        try:
            jsonschema.validate(instance=arguments, schema=spec.input_schema)
        except ValidationError as ve:
            err = f"参数校验失败: {ve.message}"
            tr = ToolResult(tool_name=tool_name, success=False, summary="", error=err)
            return json.dumps({"error": err}, ensure_ascii=False), tr

        if not self._permissions.require_capability_classes(spec, arguments):
            err = (
                "权限策略拒绝：该工具声明的访问类（网络/写入/执行/外部调用）"
                "未在配置中放行，且未获得本次会话的交互授权。"
            )
            tr = ToolResult(tool_name=tool_name, success=False, summary="", error=err)
            return json.dumps({"error": err}, ensure_ascii=False), tr

        if not self._permissions.require_approval(spec, arguments):
            err = "用户拒绝执行该工具调用"
            tr = ToolResult(tool_name=tool_name, success=False, summary="", error=err)
            return json.dumps({"error": err}, ensure_ascii=False), tr

        timeout = spec.timeout_sec
        if timeout is None:
            timeout = float(self._settings.agent.tool_timeout_sec)

        last_exc: BaseException | None = None
        for attempt in range(self._max_retries):
            try:
                coro = spec.handler(arguments)
                raw = await asyncio.wait_for(coro, timeout=timeout)
                tr = self._normalize_handler_result(tool_name, spec, raw)
                if isinstance(raw, str) and raw.strip().startswith("{"):
                    try:
                        probe = json.loads(raw)
                        if isinstance(probe, dict) and (
                            "error" in probe or "result" in probe or "payload" in probe
                        ):
                            return raw, tr
                    except (json.JSONDecodeError, TypeError):
                        pass
                content = self._result_to_tool_message_text(tr)
                return content, tr
            except asyncio.TimeoutError:
                last_exc = asyncio.TimeoutError()
                logger.error("Tool %s timed out after %ss", tool_name, timeout)
                break
            except _RETRYABLE_EXCEPTIONS as e:
                last_exc = e
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(self._backoff_base**attempt)
                else:
                    break
            except Exception as e:
                last_exc = e
                tb = traceback.format_exc()
                logger.error("Tool %s failed:\n%s", tool_name, tb)
                break

        err_msg = str(last_exc) if last_exc else "工具执行失败"
        tr = ToolResult(tool_name=tool_name, success=False, summary="", error=err_msg)
        return json.dumps({"error": err_msg}, ensure_ascii=False), tr

    def _normalize_handler_result(self, tool_name: str, spec: ToolSpec, raw: Any) -> ToolResult:
        if isinstance(raw, ToolResult):
            tr = raw
            if not tr.success:
                return tr
            body = tr.summary
            if tr.payload is not None:
                text = format_tool_result_for_model(
                    {"result": body, "payload": tr.payload},
                    max_chars=max(4000, spec.max_output_tokens * 4),
                )
            else:
                text = format_tool_result_for_model(body, max_chars=max(4000, spec.max_output_tokens * 4))
            return ToolResult(tool_name=tool_name, success=True, summary=text, payload=tr.payload)
        if isinstance(raw, str):
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                text = format_tool_result_for_model(raw, max_chars=max(4000, spec.max_output_tokens * 4))
                return ToolResult(tool_name=tool_name, success=True, summary=text)
            if isinstance(data, dict) and "error" in data:
                return ToolResult(
                    tool_name=tool_name,
                    success=False,
                    summary="",
                    error=str(data["error"]),
                )
            text = format_tool_result_for_model(raw, max_chars=max(4000, spec.max_output_tokens * 4))
            return ToolResult(
                tool_name=tool_name,
                success=True,
                summary=text,
                payload=data.get("payload") if isinstance(data, dict) else None,
            )
        if isinstance(raw, dict) and raw.get("error"):
            return ToolResult(
                tool_name=tool_name,
                success=False,
                summary="",
                error=str(raw["error"]),
            )
        text = format_tool_result_for_model(raw, max_chars=max(4000, spec.max_output_tokens * 4))
        payload = None
        if isinstance(raw, dict) and "payload" in raw:
            payload = raw.get("payload")
        return ToolResult(tool_name=tool_name, success=True, summary=text, payload=payload)

    @staticmethod
    def _result_to_tool_message_text(tr: ToolResult) -> str:
        if tr.success:
            data: dict[str, Any] = {"result": tr.summary}
            if tr.payload is not None:
                data["payload"] = tr.payload
            return json.dumps(data, ensure_ascii=False, default=str)
        return json.dumps({"error": tr.error or tr.summary or "tool failed"}, ensure_ascii=False)
