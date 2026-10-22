"""Bounded source retrieval and extraction.

Wraps the project's existing ``scrape_url`` / ``ContentExtractor`` machinery
rather than adding a second HTTP or readability stack, and adds the bounds that
stack does not have:

- **SSRF validation we perform ourselves.** ``ScrapingClient.get`` only runs its
  SSRF check when ``engine="scrape"``; a caller passing any other engine string
  gets no protection. This module always validates.
- **A content-type allowlist.** Without one the client happily decodes a PDF or
  a binary as text.
- **A response size ceiling.** There is no size limit on the scrape path today.
- **Bounded retention.** Only the passages that will actually be cited are
  kept; the full page is never stored.

Every failure is classified and returned on the source record. A page that
could not be read is recorded as unreadable rather than quietly dropped, so a
report can say why a source is missing.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from jiro.log import get_logger
from jiro.research.config import ResearchConfig
from jiro.research.urls import UnsafeURLError, validate_source_url

log = get_logger("jiro.research.fetch")

#: Extraction outcomes recorded on the source row. Anything not in this set is
#: a bug in the caller.
EXTRACTION_STATUSES = (
    "not_attempted", "extracted", "denied", "timeout", "parse_error",
    "no_content", "unsupported_type", "too_large", "ssrf_blocked",
    "robots_denied", "error",
)

#: Text that is never evidence: cookie banners, navigation chrome.
_CHROME = re.compile(
    r"^(cookie|accept all|subscribe|sign in|log in|advertisement|"
    r"skip to (main )?content|privacy policy|terms of (use|service))\b",
    re.IGNORECASE,
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
_WS = re.compile(r"[ \t\xa0]+")
_MULTI_NL = re.compile(r"\n{3,}")


def normalize_text(raw: str) -> str:
    """Collapse whitespace without destroying paragraph structure."""
    if not raw:
        return ""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = _WS.sub(" ", text)
    text = _MULTI_NL.sub("\n\n", text)
    return text.strip()


@dataclass(frozen=True)
class Passage:
    """One extractable span of text, with a stable locator."""

    text: str
    locator: str
    start: int
    end: int
    heading: str = ""

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(
            self.text.encode("utf-8", "replace")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "locator": self.locator,
            "start": self.start,
            "end": self.end,
            "heading": self.heading,
            "content_hash": self.content_hash,
        }


@dataclass
class FetchOutcome:
    """What happened when we tried to read one URL.

    ``status == "extracted"`` is the only value that means evidence exists. Every
    other value carries a reason, and the engine records it either way.
    """

    url: str
    status: str
    error: Optional[str] = None
    final_url: Optional[str] = None
    canonical_url: Optional[str] = None
    title: Optional[str] = None
    domain: str = ""
    author: Optional[str] = None
    published_at: Optional[float] = None
    content_type: Optional[str] = None
    text: str = ""
    passages: Tuple[Passage, ...] = ()
    truncated: bool = False
    char_count: int = 0
    content_hash: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "extracted"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "status": self.status,
            "error": self.error,
            "final_url": self.final_url,
            "canonical_url": self.canonical_url,
            "title": self.title,
            "domain": self.domain,
            "author": self.author,
            "published_at": self.published_at,
            "content_type": self.content_type,
            "char_count": self.char_count,
            "truncated": self.truncated,
            "content_hash": self.content_hash,
            "passage_count": len(self.passages),
        }


def _classify_exception(exc: BaseException) -> str:
    """Map a transport or parser exception onto a recorded status."""
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if isinstance(exc, asyncio.TimeoutError) or "timeout" in name or "timeout" in text:
        return "timeout"
    if "robots" in text:
        return "robots_denied"
    if any(t in name for t in ("ssl", "tls", "certificate")) or "ssl" in text:
        return "error"
    if "dns" in name or "name or service not known" in text or "nodename" in text:
        return "error"
    if any(t in text for t in ("403", "401", "forbidden", "unauthorized",
                              "paywall", "blocked")):
        return "denied"
    if any(t in name for t in ("parse", "decode", "unicode", "encoding")):
        return "parse_error"
    return "error"


def split_passages(text: str, *, max_chars: int, max_passages: int,
                   query_terms: Tuple[str, ...] = ()) -> Tuple[Passage, ...]:
    """Split text into candidate evidence passages.

    Passage selection is deliberately simple and inspectable: paragraph and
    sentence boundaries, then a relevance preference toward passages containing
    the query terms. A more clever segmenter would be harder to explain in a
    report, and this is what actually gets cited.
    """
    if not text:
        return ()
    lowered_terms = tuple(t for t in query_terms if len(t) > 2)

    scored: List[Tuple[float, int, str, str]] = []
    cursor = 0
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            cursor += 2
            continue
        for sentence in _SENTENCE_SPLIT.split(block):
            sentence = sentence.strip()
            if len(sentence) < 40 or _CHROME.match(sentence):
                continue
            start = text.find(sentence, cursor)
            if start < 0:
                start = cursor
            cursor = start + len(sentence)
            lowered = sentence.lower()
            score = min(len(sentence), 600) / 600.0
            for term in lowered_terms:
                if term in lowered:
                    score += 0.5
            scored.append((score, start, sentence, ""))

    if not scored:
        # Very short or oddly-structured page: keep the head so the source is
        # still citable, marked honestly by its length.
        head = text[:max_chars].strip()
        return (Passage(text=head, locator="document", start=0,
                        end=len(head)),) if head else ()

    scored.sort(key=lambda item: (-item[0], item[1]))
    chosen: List[Passage] = []
    seen_hashes: set = set()
    for _, start, sentence, _ in scored:
        if len(chosen) >= max_passages:
            break
        digest = hashlib.sha256(
            sentence.encode("utf-8", "replace")).hexdigest()
        if digest in seen_hashes:
            continue
        seen_hashes.add(digest)
        clipped = sentence[:max_chars]
        chosen.append(Passage(
            text=clipped,
            locator=f"char:{start}-{start + len(clipped)}",
            start=start,
            end=start + len(clipped),
        ))
    chosen.sort(key=lambda p: p.start)
    return tuple(chosen)


class SourceFetcher:
    """Fetches and extracts a single source under the configured bounds."""

    def __init__(self, config: ResearchConfig, scraper: Any = None, *,
                 allow_private: bool = False) -> None:
        """Args:
        config: bounds and the content-type allowlist.
        scraper: the project's scraping client. When ``None`` a client is
            built lazily from settings, so the fetcher is usable in tests with
            any object exposing the same ``get`` coroutine.
        """
        self._config = config
        self._scraper = scraper
        # Test-only. Never enabled from a research request.
        self._allow_private = allow_private

    def _client(self) -> Any:
        if self._scraper is None:
            raise RuntimeError(
                "SourceFetcher was built without a scraping client; pass one "
                "explicitly rather than reaching for a global")
        return self._scraper

    async def fetch(self, url: str, *,
                    query_terms: Tuple[str, ...] = ()) -> FetchOutcome:
        """Retrieve and extract one URL.

        Never raises for an unreadable source: the failure is reported in the
        returned outcome so it can be recorded on the source row. A research
        engine that aborts because one page 404s has thrown away the pages that
        did work.
        """
        from jiro.research.urls import domain_of

        domain = domain_of(url)
        try:
            safe_url = await validate_source_url(
                url, allow_private=self._allow_private)
        except UnsafeURLError as exc:
            return FetchOutcome(url=url, status=exc.reason, error=str(exc),
                                domain=domain)

        try:
            response = await asyncio.wait_for(
                self._client().get(safe_url, engine="scrape"),
                timeout=self._config.fetch_timeout_seconds,
            )
        except asyncio.TimeoutError:
            return FetchOutcome(
                url=url, status="timeout", domain=domain,
                error=f"timed out after {self._config.fetch_timeout_seconds}s")
        except BaseException as exc:
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)):
                raise
            status = _classify_exception(exc)
            return FetchOutcome(url=url, status=status, domain=domain,
                                error=f"{type(exc).__name__}: {exc}"[:500])

        body, raw_response = response if isinstance(response, tuple) else (
            response, None)
        body = body or ""

        content_type = ""
        final_url = url
        if raw_response is not None:
            headers = getattr(raw_response, "headers", None) or {}
            try:
                content_type = str(headers.get("content-type", "")).lower()
            except Exception:  # pragma: no cover - exotic header mapping
                content_type = ""
            final_url = str(getattr(raw_response, "url", url) or url)

        base_type = content_type.split(";", 1)[0].strip()
        if base_type and not any(
                base_type == allowed or base_type.startswith(allowed)
                for allowed in self._config.allowed_content_types):
            return FetchOutcome(
                url=url, status="unsupported_type", domain=domain,
                content_type=base_type,
                error=f"content type '{base_type}' is not accepted for "
                      f"evidence extraction")

        truncated = False
        if len(body) > self._config.max_page_bytes:
            body = body[: self._config.max_page_bytes]
            truncated = True

        title = canonical = author = None
        published_at = None
        text = body
        if base_type in ("text/html", "application/xhtml+xml", "") and body:
            parsed = self._extract_html(body, final_url)
            if parsed is None:
                return FetchOutcome(
                    url=url, status="parse_error", domain=domain,
                    content_type=base_type, final_url=final_url,
                    error="could not extract readable text from html")
            text, title, canonical, author, published_at = parsed

        text = normalize_text(text)
        if not text:
            return FetchOutcome(
                url=url, status="no_content", domain=domain,
                content_type=base_type, final_url=final_url,
                error="page contained no extractable text")

        if len(text) > self._config.max_retained_chars_per_source:
            text = text[: self._config.max_retained_chars_per_source]
            truncated = True

        passages = split_passages(
            text,
            max_chars=self._config.max_passage_chars,
            max_passages=self._config.max_passages_per_source,
            query_terms=query_terms,
        )
        if not passages:
            return FetchOutcome(
                url=url, status="no_content", domain=domain,
                content_type=base_type, final_url=final_url, title=title,
                error="page had text but no passage survived extraction")

        return FetchOutcome(
            url=url, status="extracted", domain=domain,
            final_url=final_url, canonical_url=canonical, title=title,
            author=author, published_at=published_at,
            content_type=base_type or None,
            text=text, passages=passages, truncated=truncated,
            char_count=len(text),
            content_hash=hashlib.sha256(
                text.encode("utf-8", "replace")).hexdigest(),
        )

    def _extract_html(self, html: str, url: str
                      ) -> Optional[Tuple[str, str, Optional[str],
                                         Optional[str], Optional[float]]]:
        """Run the project's existing readability extractor.

        Returns ``(text, title, canonical, author, published_at)`` or ``None``
        when no readable content could be found.
        """
        try:
            from jiro.extract import ContentExtractor
        except Exception as exc:  # pragma: no cover - import guard
            log.warning("extractor unavailable", extra={"error": str(exc)})
            return None
        try:
            extractor = ContentExtractor(html, url=url)
            result = extractor.extract()
            metadata = result.metadata or {}
            text = result.markdown or result.text or ""
            published = metadata.get("published_at") or metadata.get(
                "article:published_time") or metadata.get("date")
            published_at = None
            if isinstance(published, str) and published.strip():
                from jiro.research.timeutil import parse_timestamp

                parsed = parse_timestamp(published.strip())
                published_at = parsed
            return (
                text,
                result.title or None,
                metadata.get("canonical") or None,
                metadata.get("author") or None,
                published_at,
            )
        except Exception as exc:
            log.debug("html extraction failed", extra={"error": str(exc)})
            return None


__all__ = [
    "EXTRACTION_STATUSES",
    "FetchOutcome",
    "Passage",
    "SourceFetcher",
    "normalize_text",
    "split_passages",
]