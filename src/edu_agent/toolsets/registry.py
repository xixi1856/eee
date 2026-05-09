"""Tool catalog — no execution (A4)."""

from __future__ import annotations

import ast
import importlib
import logging
from pathlib import Path
from typing import Any

from edu_agent.config import EduSettings, ToolsetsSettings
from edu_agent.toolsets.models import ToolSpec

logger = logging.getLogger(__name__)


def _module_registers_tools(module_path: Path) -> bool:
    """True when module has top-level ``toolset_registry.register(...)``."""
    try:
        source = module_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(module_path))
    except (OSError, SyntaxError):
        return False

    for stmt in tree.body:
        if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)):
            continue
        func = stmt.value.func
        if not (isinstance(func, ast.Attribute) and func.attr == "register"):
            continue
        if isinstance(func.value, ast.Name) and func.value.id == "toolset_registry":
            return True
    return False


def discover_builtin_tools() -> None:
    """Import every ``tools/*.py`` that registers tools on import."""
    tools_pkg = Path(__file__).resolve().parent.parent / "tools"
    skip = {"__init__.py", "builtin_legacy.py"}
    for path in sorted(tools_pkg.glob("*.py")):
        if path.name in skip:
            continue
        if _module_registers_tools(path):
            mod_name = f"edu_agent.tools.{path.stem}"
            try:
                importlib.import_module(mod_name)
            except Exception as exc:
                logger.warning("discover_builtin_tools: failed to import %s: %s", mod_name, exc)


class ToolsetRegistry:
    """Central registry of ToolSpec entries — lookup and filtering only."""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._generation = 0

    def register(self, spec: ToolSpec, *, overwrite: bool = False) -> bool:
        if spec.name in self._specs and not overwrite:
            logger.debug("toolset_registry: %r already registered, skipping", spec.name)
            return False
        self._specs[spec.name] = spec
        self._generation += 1
        return True

    def has(self, name: str) -> bool:
        return name in self._specs

    def get_spec(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def names(self) -> set[str]:
        return set(self._specs)

    def list_specs(
        self,
        settings: EduSettings,
        *,
        disabled_names: frozenset[str] | None = None,
    ) -> list[ToolSpec]:
        """Return enabled ToolSpecs (toolset yaml + check_fn + disabled_names)."""
        disabled_names = disabled_names or frozenset()
        ts_cfg = getattr(settings, "toolsets", None)
        if not isinstance(ts_cfg, ToolsetsSettings):
            ts_cfg = ToolsetsSettings()

        out: list[ToolSpec] = []
        for spec in self._specs.values():
            if spec.name in disabled_names:
                continue
            if not ts_cfg.is_toolset_enabled(spec.toolset):
                continue
            if spec.check_fn is not None:
                try:
                    if not spec.check_fn():
                        continue
                except Exception:
                    continue
            out.append(spec)
        return sorted(out, key=lambda s: s.name)


toolset_registry = ToolsetRegistry()
