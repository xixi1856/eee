"""Basic retry classification for provider calls (A1 single-credential)."""

from __future__ import annotations

from typing import Any


def is_retryable_error(exc: BaseException) -> bool:
    """Return True when a transient failure may succeed on retry."""
    name = type(exc).__name__
    if "Timeout" in name or "timeout" in str(exc).lower():
        return True
    if "Connection" in name or "ConnectError" in name:
        return True
    # OpenAI SDK errors often expose status_code
    code = getattr(exc, "status_code", None)
    if code is not None and int(code) in (408, 429, 500, 502, 503, 504):
        return True
    return False


def with_exponential_backoff(
    attempt: int,
    *,
    base_delay_s: float = 0.5,
    max_delay_s: float = 8.0,
) -> float:
    """Compute sleep duration before attempt *attempt* (1-based)."""
    import math

    delay = base_delay_s * math.pow(2.0, float(attempt - 1))
    return min(delay, max_delay_s)
