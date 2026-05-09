"""OpenAI tools adapter — shape from neutral ToolSpec."""

from __future__ import annotations

from edu_agent.config import EduSettings
from edu_agent.llm_tools import tool_specs_to_openai_tools
from edu_agent.toolsets.models import ToolPermission, ToolSpec
from edu_agent.toolsets.registry import ToolsetRegistry


async def _noop_handler(_: dict) -> str:
    return "ok"


def test_tool_specs_to_openai_tools_shape() -> None:
    reg = ToolsetRegistry()
    reg.register(
        ToolSpec(
            name="demo_tool",
            description="Demo",
            input_schema={
                "type": "object",
                "properties": {"x": {"type": "integer"}},
                "required": ["x"],
            },
            handler=_noop_handler,
            toolset="default",
            permissions=[ToolPermission.READ],
        ),
        overwrite=True,
    )
    specs = reg.list_specs(EduSettings())
    tools = tool_specs_to_openai_tools(specs)
    assert len(tools) == 1
    t0 = tools[0]
    assert t0["type"] == "function"
    assert t0["function"]["name"] == "demo_tool"
    assert t0["function"]["description"] == "Demo"
    assert t0["function"]["parameters"]["type"] == "object"
    assert "x" in t0["function"]["parameters"]["properties"]
