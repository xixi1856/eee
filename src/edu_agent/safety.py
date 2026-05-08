"""Input / output safety filter for EduAgent.

Responsibilities
----------------
* Detect and block user inputs that contain harmful content (violence, hate
  speech, sexual content, self-harm, illegal activities).
* Strip or flag sensitive patterns from agent replies before they reach the
  user (defence-in-depth, complementing the LLM safety prompt).
* Provide a lightweight, purely rule-based first pass so that obviously bad
  requests never reach the LLM at all.

Design principles
-----------------
* No external network calls – this module must work offline.
* Conservative: prefer false positives (block borderline content) over false
  negatives.
* All category labels are kept in human-readable form so they can be logged
  and explained to the user.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Blocked pattern registry
# ---------------------------------------------------------------------------

# Each entry is (category_label, compiled_regex).
# Patterns are intentionally broad – this is a safety filter, not an NLP
# classifier.  The LLM-level safety prompt handles subtler cases.
_RAW_PATTERNS: list[tuple[str, str]] = [
    # Violence
    ("violence", r"(?:如何|怎么|怎样).{0,20}(?:杀|伤害|攻击|伤人|打人|刺|爆炸|炸弹|枪)"),
    ("violence", r"制作.{0,10}(?:武器|炸弹|毒品|爆炸物)"),
    # Self-harm
    ("self_harm", r"(?:如何|怎么).{0,20}(?:自杀|自伤|割腕|服药过量)"),
    ("self_harm", r"(?:想死|不想活|结束生命)"),
    # Sexual content
    ("sexual", r"(?:色情|裸体|性爱|做爱|强奸|猥亵)"),
    # Hate speech
    ("hate_speech", r"(?:种族歧视|性别歧视|仇恨言论)"),
    # Illegal activities
    ("illegal", r"(?:如何|怎么).{0,20}(?:黑客|入侵|破解密码|窃取|诈骗|洗钱)"),
    ("illegal", r"(?:毒品|冰毒|大麻|海洛因).{0,15}(?:购买|出售|制作|获取)"),
]

_COMPILED: list[tuple[str, re.Pattern[str]]] = [
    (label, re.compile(pattern, re.IGNORECASE))
    for label, pattern in _RAW_PATTERNS
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass
class SafetyCheckResult:
    """Result of a safety check on a single text."""

    safe: bool
    # Human-readable reason when safe=False.
    reason: str = ""
    # Category label(s) that triggered the block.
    categories: list[str] = field(default_factory=list)

    def block_message(self) -> str:
        """User-facing refusal message."""
        return (
            "抱歉，您的请求包含不适当的内容，我无法协助处理。"
            "如果您有学习上的问题，我很乐意帮助您。"
        )


def check_input(text: str) -> SafetyCheckResult:
    """Screen user input for harmful content.

    Args:
        text: Raw user message.

    Returns:
        ``SafetyCheckResult`` with ``safe=True`` if the text passes all
        checks, or ``safe=False`` with details about why it was blocked.
    """
    triggered: list[str] = []
    for label, pattern in _COMPILED:
        if pattern.search(text):
            triggered.append(label)

    if triggered:
        categories = list(dict.fromkeys(triggered))  # deduplicate, preserve order
        return SafetyCheckResult(
            safe=False,
            reason=f"检测到不适当内容类别：{', '.join(categories)}",
            categories=categories,
        )
    return SafetyCheckResult(safe=True)


def check_output(text: str) -> SafetyCheckResult:
    """Screen agent output for harmful content before returning to the user.

    Uses the same pattern set as ``check_input``.  When output is flagged,
    callers should substitute a generic apology rather than returning the
    raw text.

    Args:
        text: Agent-generated response text.

    Returns:
        ``SafetyCheckResult`` with ``safe=True`` if the output is clean.
    """
    return check_input(text)
