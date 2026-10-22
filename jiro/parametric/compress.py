"""Token estimation and budgeted claim selection."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

try:  # optional, small install
    import tiktoken

    _ENC = tiktoken.get_encoding("o200k_base")
    TOKENIZER = "tiktoken:o200k_base"
except Exception:  # pragma: no cover - depends on optional install
    _ENC = None
    TOKENIZER = "estimator:chars/4"


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    if _ENC is not None:
        return len(_ENC.encode(text))
    return max(1, (len(text) + 3) // 4)


def claim_cost(claim: Dict[str, Any]) -> int:
    # 12 tokens approximates JSON keys, source ids, and punctuation overhead.
    return estimate_tokens(str(claim.get("text") or "")) + 12


def select_claims(
    claims: List[Dict[str, Any]],
    *,
    max_tokens: int,
    reserved_tokens: int = 0,
) -> Tuple[List[Dict[str, Any]], int, int]:
    """Greedy knapsack. Returns selected, used tokens, omitted count."""
    available = max(0, max_tokens - reserved_tokens)
    selected: List[Dict[str, Any]] = []
    used = 0
    omitted = 0

    ordered = sorted(
        claims,
        key=lambda c: (
            -float(c.get("score") or 0),
            -len(c.get("source_ids") or []),
            -len(str(c.get("text") or "")),
        ),
    )
    for claim in ordered:
        cost = claim_cost(claim)
        if cost > available - used:
            omitted += 1
            continue
        selected.append(claim)
        used += cost
    return selected, used, omitted
