"""Auto-register script-backed skills as callable tools.

Design (Hermes Level-0 driven):
  • Iterates SkillEntry objects whose ``scripts`` list is non-empty.
  • For each script, locates an entry-point function (``search`` > ``run``).
  • Derives an OpenAI-compatible JSON schema from ``inspect.signature``.
  • Uses ``skill.description`` (Level-0 field) as the tool description.
  • Registers a generic handler that captures stdout via redirect_stdout so
    scripts with print-based output work without modification.
  • Registers skill-backed handlers into the central registry and refreshes
    ``tools.TOOL_SCHEMAS`` so model-visible tools update dynamically.

Safety:
  • Scripts are scanned for dangerous patterns before loading.
  • On any violation the script is skipped with a warning; the agent
    continues with the remaining skills.

Adding a new script-backed skill requires only:
  1. Create ``skills/{name}/SKILL.md`` with standard frontmatter.
  2. Create ``skills/{name}/scripts/{name}.py`` with a ``search(...)`` or
     ``run(...)`` entry point.
  3. Restart the agent – auto-registration happens at init time.
"""

from __future__ import annotations

import contextlib
import json
import importlib.util
import inspect
import io
import logging
import re
import sys
import traceback
import types
from pathlib import Path
from typing import Any

from edu_agent.skills_loader import SkillEntry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Security: patterns that disqualify a script from auto-registration.
# Each entry is a (regex_pattern, description) tuple so word-boundary checks
# can distinguish e.g. standalone ``open(`` from ``urlopen(``.
# ---------------------------------------------------------------------------

_DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    (r"\bos\.system\s*\(",        "os.system()"),
    (r"\bsubprocess\.",           "subprocess"),
    (r"\beval\s*\(",              "eval()"),
    (r"\bexec\s*\(",              "exec()"),
    (r"\b__import__\s*\(",        "__import__()"),
    (r"\bimportlib\.import_module\s*\(", "importlib.import_module()"),
    (r"(?<![a-zA-Z])open\s*\(",   "open()"),   # standalone open() — not urlopen()
    (r"\bsocket\.",               "socket"),
    (r"\bctypes\.",               "ctypes"),
    (r"\bpickle\.",               "pickle"),
]

# Allowed exception: scripts may import from the stdlib list below.
# Any other import is flagged.
_STDLIB_SAFE = {
    "sys", "io", "re", "json", "math", "time", "datetime", "pathlib",
    "urllib", "urllib.request", "urllib.parse", "urllib.error",
    "xml", "xml.etree", "xml.etree.ElementTree",
    "collections", "itertools", "functools", "typing",
    "logging", "traceback",
}

# ---------------------------------------------------------------------------
# Mapping Python annotation / default-value types → JSON schema types
# ---------------------------------------------------------------------------

_PY_TO_JSON: dict[Any, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    type(None): "string",
}

_ENTRY_POINTS = ("search", "run")


def _is_safe(script_path: Path) -> bool:
    """Return True when the script passes all safety checks."""
    try:
        source = script_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("skill_tool_registry: cannot read %s: %s", script_path, exc)
        return False

    for pattern, label in _DANGEROUS_PATTERNS:
        if re.search(pattern, source):
            logger.warning(
                "skill_tool_registry: skipping %s – contains dangerous pattern %r",
                script_path,
                label,
            )
            return False
    return True


def _load_module(script_path: Path) -> types.ModuleType | None:
    """Dynamically load a Python script as a module."""
    module_name = f"_skill_script_{script_path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        logger.warning("skill_tool_registry: failed to load %s: %s", script_path, exc)
        return None
    return module


def _build_json_schema(fn: Any) -> dict:
    """Derive an OpenAI-compatible parameters schema from a function's signature.

    Rules:
      • Parameters with a default of None get type ``string`` (nullable).
      • Parameters with a typed default (int, bool…) use the matching JSON type.
      • Parameters without a default are marked as ``required``.
      • ``*args`` and ``**kwargs`` are ignored.
    """
    sig = inspect.signature(fn)
    properties: dict[str, dict] = {}
    required: list[str] = []

    for param_name, param in sig.parameters.items():
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue

        annotation = param.annotation
        default = param.default

        # Determine JSON type
        if annotation is not inspect.Parameter.empty:
            json_type = _PY_TO_JSON.get(annotation, "string")
        elif default is not inspect.Parameter.empty and default is not None:
            json_type = _PY_TO_JSON.get(type(default), "string")
        else:
            json_type = "string"

        prop: dict[str, Any] = {"type": json_type}
        # Emit enum for known sort-like string parameters
        if param_name == "sort" and default in ("relevance", "date", "updated"):
            prop["enum"] = ["relevance", "date", "updated"]
            prop["description"] = "排序方式：relevance（默认）/ date（最新发布）/ updated（最新更新）"

        properties[param_name] = prop

        if default is inspect.Parameter.empty:
            required.append(param_name)

    schema: dict = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return schema


def _make_handler(fn: Any, tool_name: str):
    """Return a handler closure that captures stdout and returns JSON string."""

    def _handler(args: dict, **kw) -> str:
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                ret = fn(**args)
            output = buf.getvalue().strip()
            if isinstance(ret, str) and ret.strip():
                return json.dumps({"result": ret.strip()}, ensure_ascii=False)
            if ret is not None:
                return json.dumps({"result": str(ret)}, ensure_ascii=False)
            if output:
                return json.dumps({"result": output}, ensure_ascii=False)
            return json.dumps({"error": "脚本执行完成但无输出"}, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "skill_tool_registry: tool %s execution failed:\n%s",
                tool_name,
                traceback.format_exc(),
            )
            return json.dumps({"error": f"执行错误: {exc}"}, ensure_ascii=False)

    return _handler


def discover_and_register(entries: list[SkillEntry]) -> list[str]:
    """Scan *entries* for script-backed skills and register them as tools.

    Returns the list of tool names that were successfully registered.

    This function is idempotent: re-calling it will skip tools whose name is
    already present in ``TOOL_SCHEMAS``.
    """
    import edu_agent.tools as _tools  # late import to avoid circular dep
    from edu_agent.registry import registry

    existing_names: set[str] = {s["function"]["name"] for s in _tools.TOOL_SCHEMAS}
    registered: list[str] = []

    for entry in entries:
        if not entry.scripts:
            continue

        for script_path in entry.scripts:
            # Derive tool name: skill name + script stem if different
            tool_name = entry.name if script_path.stem == entry.name or len(entry.scripts) == 1 else f"{entry.name}_{script_path.stem}"

            if tool_name in existing_names:
                logger.debug(
                    "skill_tool_registry: %s already registered, skipping", tool_name
                )
                continue

            if not _is_safe(script_path):
                continue

            module = _load_module(script_path)
            if module is None:
                continue

            # Find entry-point function
            fn = None
            for ep in _ENTRY_POINTS:
                fn = getattr(module, ep, None)
                if fn is not None and callable(fn):
                    break

            if fn is None:
                logger.debug(
                    "skill_tool_registry: %s has no search()/run() entry point, skipping",
                    script_path.name,
                )
                continue

            # Build schema
            param_schema = _build_json_schema(fn)
            schema_entry = {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": entry.description or f"Skill: {entry.name}",
                    "parameters": param_schema,
                },
            }

            handler = _make_handler(fn, tool_name)

            registry.register(
                name=tool_name,
                schema=schema_entry,
                handler=handler,
                description=entry.description or f"Skill: {entry.name}",
            )
            _tools.refresh_tool_schemas()
            existing_names.add(tool_name)
            registered.append(tool_name)
            logger.info(
                "skill_tool_registry: registered tool %r from %s",
                tool_name,
                script_path.relative_to(script_path.parent.parent.parent),
            )

    return registered
