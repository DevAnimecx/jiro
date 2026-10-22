"""Claim extraction from scraped pages and search snippets.

Heuristic, deterministic, and deliberately conservative. It is not a semantic
parser; it exists to produce small, cited, deduplicated units that a token
budget can pack and a human can audit.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from jiro.parametric.guard import scan_injection

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n{2,}")
_DATE_RE = re.compile(
    r"\b(19|20)\d{2}\b|\b(January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+\d{1,2},?\s+\d{4}\b",
    re.I,
)
_NUMBER_RE = re.compile(
    r"(?<![\w.])(?:[$€£₹]\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?"
    r"(?:%|percent|million|billion|trillion|thousand|ms|s|kb|mb|gb|tb|users?|"
    r"people|customers|requests?|tokens?|x|dollars?|usd|eur|gbp|inr)\b)",
    re.I,
)
_QUOTE_RE = re.compile(r"[\"“”].{10,}?[\"“”]")
_CAPITALIZED_RE = re.compile(r"\b[A-Z][a-z]{2,}\b")
_EVIDENCE_WORDS = re.compile(
    r"\b(according to|report|study|survey|data|announced|launched|released|"
    r"found|measured|benchmark|revenue|growth|price|cost|estimate|source)\b",
    re.I,
)
_BOILERPLATE = re.compile(
    r"\b(cookie|subscribe|newsletter|sign in|log in|advertisement|all rights reserved|"
    r"share on|follow us|read more|click here|privacy policy|terms of service|"
    r"accept all|manage preferences)\b",
    re.I,
)
_MARKDOWN_NOISE = re.compile(r"[*_`#>\[\]()]|https?://\S+")


def normalize_text(text: str) -> str:
    text = _MARKDOWN_NOISE.sub(" ", text or "")
    return " ".join(text.split()).strip()


def split_sentences(text: str, *, max_sentences: int = 80) -> List[str]:
    clean = normalize_text(text)
    if not clean:
        return []
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(clean) if s.strip()]
    if len(sentences) <= 1 and len(clean) > 700:
        # No punctuation: fall back to bounded chunks rather than one giant claim.
        sentences = [clean[i : i + 400] for i in range(0, min(len(clean), 4000), 400)]
    return [s for s in sentences if 30 <= len(s) <= 700][:max_sentences]


def classify_claim(text: str) -> str:
    if _QUOTE_RE.search(text):
        return "quote"
    if _NUMBER_RE.search(text):
        return "numeric"
    if _DATE_RE.search(text):
        return "timeline"
    if len(_CAPITALIZED_RE.findall(text)) >= 2:
        return "entity"
    return "fact"


def claim_score(text: str, taint: Tuple[str, ...]) -> float:
    score = 1.0
    score += min(len(text) / 220.0, 1.4)
    if _NUMBER_RE.search(text):
        score += 1.6
    if _DATE_RE.search(text):
        score += 1.0
    if len(_CAPITALIZED_RE.findall(text)) >= 2:
        score += 0.7
    if _EVIDENCE_WORDS.search(text):
        score += 1.2
    if _BOILERPLATE.search(text):
        score -= 2.5
    if taint:
        score -= 1.0
    return round(max(0.0, score), 3)


def simhash(text: str) -> int:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    if not tokens:
        return 0
    vector = [0] * 64
    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        for bit in range(64):
            if value & (1 << bit):
                vector[bit] += 1
            else:
                vector[bit] -= 1
    result = 0
    for bit, weight in enumerate(vector):
        if weight > 0:
            result |= 1 << bit
    return result


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def extract_claims(
    text: str,
    source_id: str,
    *,
    max_claims: int = 12,
    observed_at: Optional[str] = None,
) -> List[Dict[str, Any]]:
    claims: List[Dict[str, Any]] = []
    for sentence in split_sentences(text):
        taint = scan_injection(sentence)
        kind = classify_claim(sentence)
        claims.append(
            {
                "text": sentence,
                "kind": kind,
                "source_ids": [source_id],
                "observed_at": observed_at,
                "confidence": 0.55,
                "support": "single",
                "taint": list(taint),
                "score": claim_score(sentence, taint),
                "simhash": simhash(sentence),
            }
        )
    claims.sort(key=lambda c: (-float(c["score"]), c["text"]))
    return claims[:max_claims]


def iso_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
