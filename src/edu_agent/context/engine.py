"""Layered token budget signals (API usage calibration + thresholds)."""

from __future__ import annotations

from edu_agent.context.calculator import estimate_messages_tokens_rough
from edu_agent.context.models import ContextConfig


class TokenBudgetEngine:
    """Tracks display tokens; prefers last API-reported prompt size when set."""

    def __init__(self, config: ContextConfig) -> None:
        self._config = config
        self._last_prompt_tokens: int | None = None
        self._last_completion_tokens: int | None = None

    @property
    def threshold_tokens(self) -> int:
        return max(int(self._config.model_max_tokens * self._config.token_limit_percent), 256)

    @property
    def pre_check_tokens(self) -> int:
        return max(int(self._config.model_max_tokens * self._config.pre_check_ratio), 256)

    def update_from_llm_usage(
        self,
        prompt_tokens: int | None,
        completion_tokens: int | None = None,
    ) -> None:
        if prompt_tokens is not None and prompt_tokens > 0:
            self._last_prompt_tokens = int(prompt_tokens)
        if completion_tokens is not None and completion_tokens >= 0:
            self._last_completion_tokens = int(completion_tokens)

    def display_tokens(self, messages: list[dict]) -> int:
        """Tokens for compression decisions: max(rough, last API prompt) when both exist."""
        rough = estimate_messages_tokens_rough(messages)
        if self._last_prompt_tokens is not None:
            return max(rough, self._last_prompt_tokens)
        return rough

    def should_compress_pre_check(self, messages: list[dict]) -> bool:
        """High-threshold rough signal (logging / early warning only by default)."""
        rough = estimate_messages_tokens_rough(messages)
        return rough >= self.pre_check_tokens

    def should_compress(self, messages: list[dict], *, model_name: str) -> bool:
        if not self._config.compression_enabled:
            return False
        from edu_agent.context.calculator import estimate_messages_tokens

        display = self.display_tokens(messages)
        if display >= self.threshold_tokens:
            return True
        # Also compare tiktoken sum if no API usage yet
        if self._last_prompt_tokens is None:
            est = estimate_messages_tokens(messages, model_name)
            return est >= self.threshold_tokens
        return False

    def should_call_llm_summarizer(self, messages: list[dict], *, model_name: str) -> bool:
        """Optional stricter gate before paying for summary LLM."""
        from edu_agent.context.calculator import estimate_messages_tokens

        lim = self.threshold_tokens
        mult = self._config.summary_trigger_multiplier
        display = self.display_tokens(messages)
        if display >= lim * mult:
            return True
        if self._last_prompt_tokens is None:
            return estimate_messages_tokens(messages, model_name) >= lim * mult
        return False
