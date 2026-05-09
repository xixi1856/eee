"""Provider-neutral tool specifications (A4)."""

from __future__ import annotations

from enum import Enum
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field


class ToolPermission(str, Enum):
    READ = "read"
    WRITE = "write"
    NETWORK = "network"
    EXECUTE = "execute"
    EXTERNAL = "external"


class ToolSpec(BaseModel):
    """Canonical tool definition — no OpenAI wrapper."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str
    input_schema: dict[str, Any] = Field(
        ...,
        description="JSON Schema for the tool arguments object (type object + properties).",
    )
    handler: Callable[[dict[str, Any]], Awaitable[Any]]
    toolset: str = "default"
    permissions: list[ToolPermission] = Field(default_factory=lambda: [ToolPermission.READ])
    approval_required: bool = False
    timeout_sec: float | None = None
    max_output_tokens: int = 2000
    emoji: str = "🔧"
    check_fn: Callable[[], bool] | None = None
