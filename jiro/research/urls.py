"""URL normalisation and safety for research sources.

Two distinct jobs, deliberately kept apart:

**Normalisation** decides when two URLs point at the same page. It is applied
before deduplication, so a mistake here silently merges distinct pages or splits
one page into duplicates.

**Safety** decides whether a URL may be fetched at all. The project already has
``jiro.security.async_validate_target_url``; this module calls it rather than
reimplementing SSRF defence.

A note on what is *not* normalised away: query strings are not stripped
wholesale. ``?id=1`` and ``?id=2`` are different pages, and merging them would
silently drop evidence. Only parameters known to be tracking noise are removed.
"""

from __future__ import annotations

import hashlib
import re
from typing import Iterable, List
from urllib.parse import (
    parse_qsl,
    urlencode,
    urlsplit,
    urlunsplit,
)

from jiro.errors import SSRFError
from jiro.security import async_validate_target_url

#: Parameters that identify a campaign, not a document. Removing them is safe
#: and meaningfully reduces duplicate discovery.
TRACKING_PARAMS = frozenset("""
utm_source utm_medium utm_campaign utm_term utm_content utm_id utm_name
utm_reader utm_brand utm_social utm_social-type gclid gclsrc dclid fbclid
msclkid mc_cid mc_eid igshid ref ref_src ref_url ref_amp referrer source
share_id shared_from spm scm cmpid campaign_id yclid _hsenc _hsmi
s_kwcid vero_id oly_anon_id oly_enc_id trk trkCampaign
""".split())

_DEFAULT_PORTS = {"http": "80", "https": "443"}

#: Hosts that only exist to redirect. Unwrapping them makes the dedup key the
#: destination rather than a disposable wrapper.
_REDIRECT_HOSTS = {
    "google.com", "www.google.com", "bing.com", "www.bing.com",
    "duckduckgo.com", "duckduckgo.com", "yandex.com",
}

_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.\-]*$", re.IGNORECASE)


class UnsafeURLError(ValueError):
    """A URL that must not be fetched, with the reason why."""

    def __init__(self, message: str, *, reason: str = "unsafe_url") -> None:
        super().__init__(message)
        self.reason = reason


def _strip_tracking(query: str) -> str:
    if not query:
        return ""
    pairs = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True)
             if k.lower() not in TRACKING_PARAMS]
    # Sorted so two orderings of the same query produce the same key.
    pairs.sort()
    return urlencode(pairs, doseq=True)


def normalize_url(raw: str) -> str:
    """Return a canonical form of ``raw`` suitable for deduplication.

    Lowercases scheme and host, drops the default port, removes a single
    trailing slash on a non-root path, sorts and strips tracking parameters,
    and drops the fragment (which never reaches the server).

    Never raises on a malformed URL: the input is returned stripped, because a
    result we could not parse is still a real result worth recording.

    Note:
        This is *not* the same function as ``jiro.scraping.client.normalize_url``
        (Bing redirect unwrapping) or
        ``jiro.scraping.social.normalizer.normalize_url`` (social tracking
        strip). Those are local helpers to their own callers; this one is the
        research engine's dedup key.
    """
    if not isinstance(raw, str):
        return ""
    text = raw.strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return text

    scheme = (parts.scheme or "").lower()
    if not scheme or not _SCHEME_RE.match(scheme):
        # Protocol-relative or malformed: leave it alone rather than guess.
        return text

    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]

    port = parts.port
    netloc = host
    if port is not None and str(port) != _DEFAULT_PORTS.get(scheme):
        netloc = f"{host}:{port}"

    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    if path == "":
        path = "/"

    query = _strip_tracking(parts.query)
    return urlunsplit((scheme, netloc, path, query, ""))


def domain_of(url: str) -> str:
    """Registrable-ish domain for a URL, lowercased, without ``www.``.

    Used for source-independence counting, so it errs toward the *specific*
    host: two different subdomains of one publisher are usually the same
    publisher, but two unrelated domains are not.
    """
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def content_hash(text: str) -> str:
    """Stable content fingerprint used for evidence deduplication."""
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def stable_id(prefix: str, *parts: str) -> str:
    """Deterministic id derived from its parts.

    Deterministic ids matter for restart safety: a resumed run regenerating the
    same source must produce the same id, or deduplication silently fails and
    the budget gets charged twice.
    """
    digest = hashlib.sha256(
        "\x1f".join(parts).encode("utf-8", "replace")).hexdigest()
    return f"{prefix}_{digest[:24]}"


async def validate_source_url(url: str, *, allow_private: bool = False) -> str:
    """Validate a URL for fetching, applying the project's SSRF policy.

    Returns the URL on success. Raises :class:`UnsafeURLError` otherwise.

    This calls ``async_validate_target_url`` directly rather than relying on
    ``ScrapingClient.get``, whose SSRF check only runs when ``engine="scrape"``
    is passed — an easy way to fetch without a check by accident.

    Args:
        allow_private: permits private and loopback address ranges. This exists
            so tests can run a controlled local fixture server. It defaults to
            ``False``, must stay ``False`` in any deployed configuration, and
            the engine never enables it from a research request — a caller
            cannot ask for it.
    """
    if not isinstance(url, str) or not url.strip():
        raise UnsafeURLError("empty url", reason="empty_url")
    candidate = url.strip()
    scheme = candidate.split(":", 1)[0].lower() if ":" in candidate else ""
    if scheme not in ("http", "https"):
        raise UnsafeURLError(
            f"unsupported url scheme '{scheme or 'none'}'",
            reason="unsupported_scheme",
        )
    try:
        return await async_validate_target_url(
            candidate, allow_private=allow_private)
    except SSRFError as exc:
        raise UnsafeURLError(
            f"url blocked by network policy: {exc}",
            reason="ssrf_blocked") from exc
    except ValueError as exc:
        raise UnsafeURLError(f"invalid url: {exc}", reason="invalid_url") from exc


def normalize_query(text: str) -> str:
    """Normalised query text for the issued-query dedup index."""
    return " ".join(str(text or "").lower().split())


def dedupe_urls(urls: Iterable[str]) -> List[str]:
    """Order-preserving dedup on normalised URL."""
    seen: set = set()
    out: List[str] = []
    for url in urls:
        key = normalize_url(url)
        if key and key not in seen:
            seen.add(key)
            out.append(url)
    return out


__all__ = [
    "TRACKING_PARAMS",
    "UnsafeURLError",
    "content_hash",
    "dedupe_urls",
    "domain_of",
    "normalize_query",
    "normalize_url",
    "stable_id",
    "validate_source_url",
]