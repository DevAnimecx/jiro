"""Conservative prompt-injection and instruction-boundary heuristics.

These checks do not make a page safe. They mark claims so downstream code can
quarantine them, lower their rank, and keep page text out of tool metadata.
"""

from __future__ import annotations

import re
from typing import Tuple

_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    (
        "instruction_override",
        re.compile(
            r"\b(ignore|disregard|forget)\s+(all\s+)?(previous|prior|above|earlier)\s+"
            r"(instructions?|prompts?|rules?)\b",
            re.I,
        ),
    ),
    (
        "role_hijack",
        re.compile(r"\b(you are now|act as|pretend to be|new system prompt)\b", re.I),
    ),
    (
        "prompt_exfiltration",
        re.compile(
            r"\b(reveal|show|print|repeat|leak)\s+(your|the)\s+"
            r"(system prompt|instructions|hidden prompt|developer message)\b",
            re.I,
        ),
    ),
    (
        "tool_invocation",
        re.compile(
            r"\b(call|invoke|execute|run)\s+(the\s+)?(tool|function|command|shell)\b",
            re.I,
        ),
    ),
    (
        "credential_request",
        re.compile(
            r"\b(send|provide|enter|paste)\s+(your\s+)?(api key|password|token|secret)\b",
            re.I,
        ),
    ),
    (
        "data_exfiltration",
        re.compile(
            r"\b(upload|send|post|exfiltrate)\s+.{0,40}\b(to|at)\s+https?://",
            re.I,
        ),
    ),
)


def scan_injection(text: str) -> Tuple[str, ...]:
    """Return stable taint labels found in ``text``.

    Empty tuple means "no known pattern", not "safe".
    """
    if not text:
        return ()
    found = []
    for label, pattern in _PATTERNS:
        if pattern.search(text):
            found.append(label)
    return tuple(found)


def has_injection(text: str) -> bool:
    return bool(scan_injection(text))
