"""Central tool registry for EduAgent.

Follows the Hermes-agent ToolRegistry pattern:
  - Tool modules call ``registry.register()`` at module level on import.
  - ``discover_builtin_tools()`` AST-scans ``tools/*.py`` to auto-import.
  - ``dispatch()`` calls ``handler(args: dict, **kwargs)`` → JSON string.
  - ``tool_result()`` / ``tool_error()`` are helpers for handler authors.

Separation of concerns
-----------------------
  Tools  — callable Python handlers registered here; LLM can invoke them.
  Skills — SKILL.md knowledge documents + optional scripts/ that auto-wrap
           into Tools at startup via skill_tool_registry.discover_and_register().
"""

from __future__ import annotations

import ast
import importlib
import json
import logging
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Handler output helpers  (imported by every tool module)
# ---------------------------------------------------------------------------

def tool_result(data: Any, **extra: Any) -> str:
    """Serialise a successful tool result to a JSON string.

    ``data`` becomes ``result`` in the JSON object; any ``**extra`` key-value
    pairs are merged in at the top level (e.g. ``payload=...``).
    """
    payload: dict[str, Any] = {"result": data}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False, default=str)


def tool_error(msg: str) -> str:
    """Serialise a tool failure to a JSON error string."""
    return json.dumps({"error": msg}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# ToolEntry
# ---------------------------------------------------------------------------

@dataclass
class ToolEntry:
    """Metadata for a single registered tool."""

    name: str
    schema: dict                          # inner schema: {name, description, parameters}
    handler: Callable[..., Any]
    toolset: str = "default"
    description: str = ""
    emoji: str = "🔧"
    check_fn: Callable[[], bool] | None = None
    is_async: bool = False
    max_result_size_chars: int | None = None


# ---------------------------------------------------------------------------
# Discovery helper
# ---------------------------------------------------------------------------

def _module_registers_tools(module_path: Path) -> bool:
    """Return True when the module has a top-level ``registry.register(...)`` call."""
    try:
        source = module_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(module_path))
    except (OSError, SyntaxError):
        return False

    for stmt in tree.body:
        if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)):
            continue
        func = stmt.value.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "register"
            and isinstance(func.value, ast.Name)
            and func.value.id == "registry"
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------

class ToolRegistry:
    """Central registry for all callable tools.

    Usage
    -----
    Built-in tools call ``registry.register()`` at module import time.
    Skill-derived tools call ``registry.register()`` at agent startup.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolEntry] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        schema: dict,
        handler: Callable[..., Any],
        *,
        toolset: str = "default",
        description: str = "",
        emoji: str = "🔧",
        check_fn: Callable[[], bool] | None = None,
        is_async: bool = False,
        max_result_size_chars: int | None = None,
        overwrite: bool = False,
    ) -> bool:
        """Register a tool by name.

        ``schema`` may be either the inner format ``{name, description, parameters}``
        or the legacy outer format ``{"type": "function", "function": {...}}``;
        the outer wrapper is stripped automatically and re-added by
        ``get_tool_definitions()``.

        Returns True if the tool was newly registered, False if skipped.
        """
        if name in self._tools and not overwrite:
            logger.debug("registry: %r already registered, skipping", name)
            return False

        # Auto-normalise: unwrap outer {"type": "function", "function": ...}
        if schema.get("type") == "function" and "function" in schema:
            schema = schema["function"]

        if not description:
            description = schema.get("description", name)

        self._tools[name] = ToolEntry(
            name=name,
            schema=schema,
            handler=handler,
            toolset=toolset,
            description=description,
            emoji=emoji,
            check_fn=check_fn,
            is_async=is_async,
            max_result_size_chars=max_result_size_chars,
        )
        return True

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def has(self, name: str) -> bool:
        """Return True if *name* is registered."""
        return name in self._tools

    def names(self) -> set[str]:
        """Return the set of all registered tool names."""
        return set(self._tools)

    def get_tool_definitions(self) -> list[dict]:
        """Return OpenAI-compatible schemas with outer ``{"type": "function"}`` wrapper.

        Tools whose ``check_fn`` returns False are excluded.
        """
        result = []
        for entry in self._tools.values():
            if entry.check_fn is not None:
                try:
                    if not entry.check_fn():
                        continue
                except Exception:
                    continue
            result.append({"type": "function", "function": entry.schema})
        return result

    def get_schemas(self) -> list[dict]:
        """Alias for ``get_tool_definitions()`` — backward compatibility."""
        return self.get_tool_definitions()

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    @staticmethod
    def _to_json_string(name: str, result: Any) -> str:
        """Normalise a handler return value to a JSON string.

        Handles three cases:
        - str  → returned as-is (new hermes-agent style)
        - ToolResult → converted for backward compat with legacy handlers
        - anything else → wrapped in {"result": str(value)}
        """
        if isinstance(result, str):
            return result
        # Lazy import to avoid making types a hard dependency at module level
        try:
            from edu_agent.types import ToolResult
            if isinstance(result, ToolResult):
                if result.success:
                    data: dict[str, Any] = {"result": result.summary}
                    if result.payload is not None:
                        data["payload"] = result.payload
                    return json.dumps(data, ensure_ascii=False)
                return json.dumps(
                    {"error": result.error or result.summary or "tool failed"},
                    ensure_ascii=False,
                )
        except ImportError:
            pass
        return json.dumps({"result": str(result)}, ensure_ascii=False)

    def dispatch(self, name: str, args: dict[str, Any], **kwargs: Any) -> str:
        """Execute tool *name* with *args* dict.

        Calls ``handler(args, **kwargs)``.  Never raises — all exceptions are
        caught and returned as JSON error strings.
        """
        entry = self._tools.get(name)
        if entry is None:
            return json.dumps({"error": f"未知工具：{name}"}, ensure_ascii=False)
        try:
            result = entry.handler(args, **kwargs)
            return self._to_json_string(name, result)
        except Exception:  # noqa: BLE001
            tb = traceback.format_exc()
            logger.error("registry.dispatch(%s) unexpected error:\n%s", name, tb)
            return json.dumps({"error": "工具执行时发生意外错误，详见日志"}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Module-level singleton — import this everywhere
# ---------------------------------------------------------------------------

registry = ToolRegistry()


def discover_builtin_tools() -> None:
    """AST-scan ``tools/*.py`` and import every module with top-level register calls."""
    tools_pkg = Path(__file__).resolve().parent / "tools"
    skip = {"__init__.py", "builtin_legacy.py"}
    for path in sorted(tools_pkg.glob("*.py")):
        if path.name in skip:
            continue
        if _module_registers_tools(path):
            mod_name = f"edu_agent.tools.{path.stem}"
            try:
                importlib.import_module(mod_name)
            except Exception as exc:
                logger.warning(
                    "discover_builtin_tools: failed to import %s: %s", mod_name, exc
                )
