"""LLM-facing adapters for tool declarations (A4)."""

from edu_agent.llm_tools.openai import tool_specs_to_openai_tools

__all__ = ["tool_specs_to_openai_tools"]
