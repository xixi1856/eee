"""Tool permission checks, capability classes, and interactive approval (A4)."""

from __future__ import annotations

import json
import logging

from edu_agent.config import ToolPermissionPolicy
from edu_agent.toolsets.models import ToolPermission, ToolSpec

logger = logging.getLogger(__name__)

_CLASS_LABELS: dict[ToolPermission, str] = {
    ToolPermission.READ: "只读",
    ToolPermission.WRITE: "写入/修改",
    ToolPermission.NETWORK: "网络访问",
    ToolPermission.EXECUTE: "执行命令/代码",
    ToolPermission.EXTERNAL: "外部系统调用",
}


def resolve_effective_permission_policy(
    policy: ToolPermissionPolicy,
    *,
    allow_network: bool,
    allow_write: bool,
    allow_execute: bool,
    allow_external: bool,
) -> ToolPermissionPolicy:
    """Merge YAML policy with session/CLI overrides (OR per flag)."""
    return ToolPermissionPolicy(
        allow_network=policy.allow_network or allow_network,
        allow_write=policy.allow_write or allow_write,
        allow_execute=policy.allow_execute or allow_execute,
        allow_external=policy.allow_external or allow_external,
    )


def permissive_permission_policy() -> ToolPermissionPolicy:
    """All capability classes allowed (unit tests / trusted harness)."""
    return ToolPermissionPolicy(
        allow_network=True,
        allow_write=True,
        allow_execute=True,
        allow_external=True,
    )


class PermissionChecker:
    """Gates ``ToolPermission`` classes and ``approval_required`` tools.

    ``approve_all`` skips only ``approval_required`` prompts — it does **not**
    bypass NETWORK/WRITE/EXECUTE/EXTERNAL capability policy.
    """

    def __init__(
        self,
        policy: ToolPermissionPolicy,
        *,
        approve_all: bool = False,
        interactive: bool = True,
    ) -> None:
        self._policy = policy
        self._approve_all = approve_all
        self._interactive = interactive
        self._session_ok: set[tuple[str, str]] = set()
        self._class_grants: set[tuple[str, str, str]] = set()

    @property
    def policy(self) -> ToolPermissionPolicy:
        return self._policy

    def set_approve_all(self, value: bool) -> None:
        self._approve_all = value

    def _args_fingerprint(self, tool_name: str, arguments: dict) -> str:
        try:
            return json.dumps({"n": tool_name, "a": arguments}, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            return f"{tool_name}:{id(arguments)}"

    def _class_key(self, spec: ToolSpec, arguments: dict, perm: ToolPermission) -> tuple[str, str, str]:
        return (spec.name, self._args_fingerprint(spec.name, arguments), perm.value)

    def require_capability_classes(self, spec: ToolSpec, arguments: dict) -> bool:
        """Return True when all declared permission classes are allowed for this call."""
        for perm in spec.permissions:
            if perm == ToolPermission.READ:
                continue
            if perm == ToolPermission.WRITE:
                if self._policy.allow_write:
                    continue
                if spec.approval_required:
                    continue
                if not self._ensure_class_grant(spec, arguments, perm):
                    return False
                continue
            if perm == ToolPermission.NETWORK:
                if self._policy.allow_network:
                    continue
                if not self._ensure_class_grant(spec, arguments, perm):
                    return False
                continue
            if perm == ToolPermission.EXECUTE:
                if self._policy.allow_execute:
                    continue
                if not self._ensure_class_grant(spec, arguments, perm):
                    return False
                continue
            if perm == ToolPermission.EXTERNAL:
                if self._policy.allow_external:
                    continue
                if not self._ensure_class_grant(spec, arguments, perm):
                    return False
                continue
        return True

    def _ensure_class_grant(self, spec: ToolSpec, arguments: dict, perm: ToolPermission) -> bool:
        key = self._class_key(spec, arguments, perm)
        if key in self._class_grants:
            return True
        if not self._interactive:
            logger.warning(
                "PermissionChecker: blocking %s (needs %s, non-interactive)",
                spec.name,
                perm.value,
            )
            return False
        label = _CLASS_LABELS.get(perm, perm.value)
        try:
            ans = input(
                f"\n[权限] 工具 `{spec.name}` 需要 **{label}**（策略未放行）。\n"
                f"参数摘要: {str(arguments)[:200]}\n本次会话允许此类调用? [y/N]: "
            ).strip().lower()
        except EOFError:
            return False
        if ans in ("y", "yes"):
            self._class_grants.add(key)
            return True
        return False

    def require_approval(self, spec: ToolSpec, arguments: dict) -> bool:
        """Return True if execution may proceed (``approval_required`` gate only)."""
        if self._approve_all or not spec.approval_required:
            return True
        key = (spec.name, self._args_fingerprint(spec.name, arguments))
        if key in self._session_ok:
            return True
        if not self._interactive:
            logger.warning("PermissionChecker: blocking %s (non-interactive)", spec.name)
            return False
        try:
            ans = input(
                f"\n[权限] 工具 `{spec.name}` 需要确认。\n"
                f"参数摘要: {str(arguments)[:200]}\n是否允许执行? [y/N]: "
            ).strip().lower()
        except EOFError:
            return False
        if ans in ("y", "yes"):
            self._session_ok.add(key)
            return True
        return False
