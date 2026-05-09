"""Context budgeting and compaction."""

from edu_agent.context.calculator import (
    estimate_messages_tokens,
    estimate_messages_tokens_rough,
    estimate_tokens,
    get_context_limit,
    pre_check_limit,
)
from edu_agent.context.compressor import (
    COMPACTION_FAILURE_SNIPPET,
    ContextOverflowError,
    compress_messages,
    format_compaction_summary_body,
    sanitize_tool_pairs,
    trim_until_under_token_limit,
)
from edu_agent.context.engine import TokenBudgetEngine
from edu_agent.context.manager import ContextManager
from edu_agent.context.models import ContextConfig

__all__ = [
    "ContextConfig",
    "ContextManager",
    "ContextOverflowError",
    "TokenBudgetEngine",
    "COMPACTION_FAILURE_SNIPPET",
    "compress_messages",
    "format_compaction_summary_body",
    "sanitize_tool_pairs",
    "estimate_messages_tokens",
    "estimate_messages_tokens_rough",
    "estimate_tokens",
    "get_context_limit",
    "pre_check_limit",
    "trim_until_under_token_limit",
]
