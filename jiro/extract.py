"""Universal web scraper: URL → clean, structured content.

Implements a lightweight readability algorithm (content scoring by text
density / link density), metadata extraction (OpenGraph, Twitter Cards,
JSON-LD) and a compact HTML→Markdown converter. Pure selectolax — no heavy
dependencies.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from selectolax.parser import HTMLParser, Node

from jiro.errors import ScrapeError

TAG_BLOCK = {"p", "div", "section", "article", "main", "li", "blockquote", "pre",
             "table", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "figure"}
TAG_IGNORE = {"script", "style", "noscript", "template", "svg", "iframe", "form",
              "button", "nav", "aside", "footer", "header", "figure", "figcaption"}
SKIP_ATTR = {"hidden"}

UNWANTED_CLASS_HINTS = ("nav", "menu", "sidebar", "comment", "footer", "header",
                        "advert", "social", "share", "related", "promo", "cookie",
                        "popup", "modal", "banner", "newsletter", "subscribe")
WANTED_CLASS_HINTS = ("content", "article", "post", "entry", "body", "main", "text")

MARKDOWN_ESCAPE = re.compile(r"([\\`*_{}\[\]()#+.!|>~-])")


class ExtractResult:
    def __init__(self) -> None:
        self.title: str = ""
        self.metadata: Dict[str, Any] = {}
        self.html: str = ""
        self.text: str = ""
        self.markdown: str = ""
        self.links: List[Dict[str, Any]] = []
        self.images: List[Dict[str, Any]] = []
        self.json_ld: List[Dict[str, Any]] = []


class ContentExtractor:
    def __init__(self, html: str, url: str = "") -> None:
        self.tree = HTMLParser(html)
        self.url = url
        self.base = self._base_url()

    def _base_url(self) -> str:
        base = self.tree.css_first("base[href]")
        if base and base.attributes.get("href"):
            return urljoin(self.url, base.attributes["href"])
        return self.url

    # ------------------------------------------------------------------ meta
    def extract(self) -> ExtractResult:
        result = ExtractResult()
        result.title = self.title()
        result.metadata = self.metadata()
        result.json_ld = self.json_ld_blocks()
        if result.json_ld:
            result.metadata["json_ld"] = result.json_ld

        main = self.main_content()
        if main is not None:
            result.html = main.html or ""
            result.text = main.text(separator="\n", strip=True)
            result.markdown = node_to_markdown(main, base_url=self.base)
        else:
            body = self.tree.body
            if body is not None:
                result.text = body.text(separator="\n", strip=True)
                result.markdown = node_to_markdown(body, base_url=self.base)

        result.links = self.links()
        result.images = self.images()
        return result

    def title(self) -> str:
        for sel in ("meta[property='og:title']", "meta[name='twitter:title']",
                    "meta[name='title']"):
            node = self.tree.css_first(sel)
            if node and (node.attributes.get("content") or "").strip():
                return (node.attributes.get("content") or "").strip()
        t = self.tree.css_first("title")
        if t and t.text().strip():
            return t.text().strip()
        h1 = self.tree.css_first("h1")
        return h1.text(strip=True) if h1 else ""

    def metadata(self) -> Dict[str, Any]:
        meta: Dict[str, Any] = {}
        for node in self.tree.css("meta"):
            attrs = node.attributes
            name = attrs.get("name") or attrs.get("property") or attrs.get("itemprop")
            content = attrs.get("content")
            if name and content and name not in meta:
                meta[name] = content
        canonical = self.tree.css_first("link[rel='canonical']")
        if canonical:
            meta["canonical"] = canonical.attributes.get("href", "")
        return meta

    def json_ld_blocks(self) -> List[Dict[str, Any]]:
        blocks = []
        for script in self.tree.css("script[type='application/ld+json']"):
            try:
                data = json.loads(script.text())
                blocks.append(data if isinstance(data, list) else [data])
            except Exception:
                continue
        return [item for group in blocks for item in group][:20]

    # --------------------------------------------------------- main content
    def main_content(self) -> Optional[Node]:
        candidates: List[Node] = []
        for node in self.tree.css("article, main, [role='main'], .article, .post, "
                                   ".entry-content, .post-content, .content, #content, "
                                   "#main, #article"):
            if node.tag in TAG_IGNORE:
                continue
            candidates.append(node)
        if not candidates:
            body = self.tree.body
            if body is not None:
                candidates = [body]
        if not candidates:
            return None

        best = max(candidates, key=self._score)
        best_score = self._score(best)
        # If even the best candidate is thin, fall back to the body text
        if best_score < 20:
            body = self.tree.body
            if body is not None and body is not best:
                best = body
        return best

    def _score(self, node: Node) -> float:
        text = node.text()
        length = len(re.sub(r"\s+", " ", text).strip())
        paragraphs = node.css("p")
        para_text = sum(len(p.text(strip=True)) for p in paragraphs)
        links = node.css("a")
        link_text = sum(len(a.text(strip=True)) for a in links)
        link_density = (link_text / length) if length else 1.0
        score = min(para_text, length) * (1.0 - min(link_density, 0.6))
        # bonus for semantic structure
        if node.css_first("h1, h2, h3"):
            score *= 1.2
        return score

    # ------------------------------------------------------------ links/images
    def links(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        seen = set()
        for a in self.tree.css("a[href]"):
            href = (a.attributes.get("href") or "").strip()
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            url = urljoin(self.base, href)
            if url in seen:
                continue
            seen.add(url)
            out.append({"text": a.text(strip=True)[:200], "url": url})
            if len(out) >= 200:
                break
        return out

    def images(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        seen = set()
        for img in self.tree.css("img[src]"):
            src = (img.attributes.get("src") or "").strip()
            if not src or src.startswith("data:"):
                continue
            url = urljoin(self.base, src)
            if url in seen:
                continue
            seen.add(url)
            out.append({
                "url": url,
                "alt": img.attributes.get("alt", ""),
                "width": img.attributes.get("width", ""),
                "height": img.attributes.get("height", ""),
            })
            if len(out) >= 100:
                break
        return out


# --------------------------------------------------------------------------
# HTML → Markdown
# --------------------------------------------------------------------------
def node_to_markdown(node: Node, base_url: str = "", depth: int = 0) -> str:
    if depth > 14:
        return ""
    if node.tag in TAG_IGNORE:
        return ""
    if node.attributes.get("hidden") is not None:
        return ""

    tag = node.tag
    if tag in ("script", "style", "noscript", "template", "svg", "iframe", "form"):
        return ""

    if tag == "br":
        return "\n"
    if tag == "hr":
        return "\n---\n"
    if tag == "img":
        src = node.attributes.get("src", "")
        alt = node.attributes.get("alt", "")
        url = urljoin(base_url, src) if src else ""
        return f"![{alt}]({url})" if url else ""
    if tag == "a":
        href = node.attributes.get("href", "")
        url = urljoin(base_url, href) if href else ""
        text = node.text(strip=True)
        if not text:
            return ""
        return f"[{text}]({url})" if url else text
    if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        level = int(tag[1])
        text = node.text(strip=True)
        return f"\n{'#' * level} {text}\n" if text else ""
    if tag == "p":
        inner = "".join(node_to_markdown(child, base_url, depth + 1)
                        for child in node.iter())
        return "\n" + (inner or node.text(strip=True)) + "\n"
    if tag == "li":
        marker = "- " if node.parent and node.parent.tag == "ul" else "1. "
        inner = "".join(node_to_markdown(child, base_url, depth + 1)
                        for child in node.iter())
        return f"{marker}{(inner or node.text(strip=True))}"
    if tag in ("ul", "ol"):
        parts = []
        for child in node.iter():
            parts.append(node_to_markdown(child, base_url, depth + 1))
        return "\n" + "\n".join(p for p in parts if p) + "\n"
    if tag == "blockquote":
        text = node.text(strip=True)
        return f"\n> {text}\n" if text else ""
    if tag == "pre":
        return f"\n```\n{node.text()}\n```\n"
    if tag == "code":
        return f"`{node.text(strip=True)}`"
    if tag == "strong" or tag == "b":
        return f"**{node.text(strip=True)}**"
    if tag in ("em", "i"):
        return f"*{node.text(strip=True)}*"
    if tag == "table":
        rows = []
        for tr in node.css("tr"):
            cells = [td.text(strip=True).replace("\n", " ") for td in tr.css("td, th")]
            rows.append("| " + " | ".join(cells) + " |")
        if rows:
            header = rows[0]
            sep = "|" + "---|" * (header.count("|") - 1)
            return "\n" + "\n".join([header, sep] + rows[1:]) + "\n"
    if tag == "figure":
        return "\n" + node.text(strip=True) + "\n"

    parts = []
    for child in node.iter(include_text=True):
        if child.tag in TAG_IGNORE:
            continue
        if child.tag is None:  # text node
            text = child.text() if hasattr(child, "text") else str(child)
            if text and text.strip():
                parts.append(re.sub(r"\s+", " ", text))
        else:
            parts.append(node_to_markdown(child, base_url, depth + 1))
    return "".join(parts)


def _strip_markdown(text: str) -> str:
    return re.sub(r"[#>*_`\[\]()!|~-]", "", text)


# --------------------------------------------------------------------------
# Fetching wrapper used by the /scrape endpoint
# --------------------------------------------------------------------------
async def scrape_url(url: str, client: Any, *, fmt: str = "markdown",
                     include_metadata: bool = True,
                     include_structured: bool = False,
                     html: Optional[str] = None,
                     response: Any = None) -> Dict[str, Any]:
    """Fetch + extract a URL. Returns a ScrapeResponse-compatible dict.

    ``html``/``response`` may be passed in to avoid a second fetch (used by
    the /scrape endpoint when a custom recipe needs the raw HTML).
    """
    if not url.startswith(("http://", "https://")):
        raise ScrapeError("url must start with http:// or https://",
                          status_code=422)
    if html is None or response is None:
        try:
            html, response = await client.get(url, engine="scrape", raw=True)
        except Exception as exc:
            raise ScrapeError(f"failed to fetch {url}: {exc}") from exc

    extractor = ContentExtractor(html, url=url)
    result = extractor.extract()

    payload: Dict[str, Any] = {
        "url": url,
        "title": result.title,
        "links": result.links,
        "images": result.images,
        "status_code": response.status_code,
    }
    if fmt == "markdown":
        payload["content"] = result.markdown or result.text
    elif fmt == "text":
        payload["content"] = result.text
    elif fmt == "html":
        payload["content"] = result.html
    elif fmt == "json":
        payload["content"] = {
            "markdown": result.markdown,
            "text": result.text,
            "html": result.html,
            "json_ld": result.json_ld,
        }
    if include_metadata:
        payload["metadata"] = result.metadata
    if include_structured:
        from jiro.structured import extract_structured
        structured = extract_structured(html, url)
        payload["structured"] = {
            "schema_org": {
                "type": structured.schema_org.type,
                "name": structured.schema_org.name,
                "description": structured.schema_org.description,
                "url": structured.schema_org.url,
                "image": structured.schema_org.image,
                "date_published": structured.schema_org.date_published,
                "author": structured.schema_org.author,
                "publisher": structured.schema_org.publisher,
                "price": structured.schema_org.price,
                "price_currency": structured.schema_org.price_currency,
                "availability": structured.schema_org.availability,
                "brand": structured.schema_org.brand,
                "rating": structured.schema_org.rating,
                "review_count": structured.schema_org.review_count,
                "duration": structured.schema_org.duration,
                "recipe_ingredients": structured.schema_org.recipe_ingredients,
                "recipe_instructions": structured.schema_org.recipe_instructions,
                "faq_items": structured.schema_org.faq_items,
                "event_start": structured.schema_org.event_start,
                "event_end": structured.schema_org.event_end,
                "event_location": structured.schema_org.event_location,
                "job_title": structured.schema_org.job_title,
                "job_company": structured.schema_org.job_company,
                "job_location": structured.schema_org.job_location,
            },
            "classification": {
                "primary_type": structured.classification.primary_type,
                "confidence": structured.classification.confidence,
                "secondary_types": structured.classification.secondary_types,
            },
            "answers": [
                {"answer": a.answer, "type": a.answer_type,
                 "confidence": a.confidence}
                for a in structured.answers
            ],
            "images_with_alt": structured.images_with_alt[:20],
            "microdata_count": len(structured.microdata),
        }
    return payload
