"""Structured data extraction — Schema.org auto-extraction, content classification,
answer extraction, and image alt-text extraction.

Parses JSON-LD, Microdata, and RDFa from HTML. Classifies page content by type
(article, product, video, recipe, FAQ, etc.) and extracts direct answers,
featured snippets, knowledge panels, and FAQ data.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

from selectolax.parser import HTMLParser, Node

from jiro.log import get_logger

log = get_logger("jiro.structured")

# ── Content type classification signals ──────────────────────────────────

_ARTICLE_SIGNALS = {
    "schema_types": {"Article", "NewsArticle", "BlogPosting", "WebPage",
                     "TechArticle", "ScholarlyArticle", "Report"},
    "meta_tags": {"article:published_time", "article:modified_time",
                  "article:author", "article:section"},
    "html_signals": ["article", "[role='article']", ".article-body",
                     ".post-content", ".entry-content", ".story-body"],
}

_PRODUCT_SIGNALS = {
    "schema_types": {"Product", "Offer", "AggregateOffer"},
    "meta_tags": {"product:price:amount", "product:price:currency",
                  "og:type"},
    "html_signals": [".product", "#product", ".product-detail",
                     ".product-info", "[data-product-id]"],
}

_VIDEO_SIGNALS = {
    "schema_types": {"VideoObject", "Video"},
    "meta_tags": {"og:video", "og:video:width", "og:video:height",
                  "video:duration"},
    "html_signals": ["video", ".video-player", ".video-container",
                     "iframe[src*='youtube']", "iframe[src*='vimeo']"],
}

_RECIPE_SIGNALS = {
    "schema_types": {"Recipe"},
    "meta_tags": {"og:type"},
    "html_signals": [".recipe", ".recipe-body", ".recipe-instructions",
                     "[itemtype*='Recipe']"],
}

_FAQ_SIGNALS = {
    "schema_types": {"FAQPage"},
    "meta_tags": {},
    "html_signals": [".faq", ".accordion", "[data-toggle='collapse']",
                     "details", "summary"],
}

_LOCAL_BUSINESS_SIGNALS = {
    "schema_types": {"LocalBusiness", "Restaurant", "Store", "MedicalBusiness",
                     "BeautySalon", "Gym", "Hotel"},
    "meta_tags": {"og:latitude", "og:longitude", "place:location:latitude"},
    "html_signals": [".business", ".store", ".restaurant"],
}

_EVENT_SIGNALS = {
    "schema_types": {"Event", "BusinessEvent", "MusicEvent", "SportsEvent"},
    "meta_tags": {"event:start_time", "event:end_time"},
    "html_signals": [".event", ".event-details"],
}

_JOB_SIGNALS = {
    "schema_types": {"JobPosting"},
    "meta_tags": {},
    "html_signals": [".job", ".job-listing", ".job-description"],
}

_REVIEW_SIGNALS = {
    "schema_types": {"Review", "AggregateRating", "Product"},
    "meta_tags": {},
    "html_signals": [".review", ".rating", ".stars", "[itemprop='ratingValue']"],
}


@dataclass
class SchemaOrgData:
    """Normalized Schema.org structured data."""
    type: str = ""
    name: str = ""
    description: str = ""
    url: str = ""
    image: str = ""
    date_published: str = ""
    date_modified: str = ""
    author: str = ""
    publisher: str = ""
    # Product-specific
    price: str = ""
    price_currency: str = ""
    availability: str = ""
    brand: str = ""
    rating: Optional[float] = None
    review_count: Optional[int] = None
    # Video-specific
    duration: str = ""
    upload_date: str = ""
    embed_url: str = ""
    # Recipe-specific
    recipe_ingredients: List[str] = field(default_factory=list)
    recipe_instructions: List[str] = field(default_factory=list)
    recipe_yield: str = ""
    recipe_cook_time: str = ""
    recipe_prep_time: str = ""
    # Event-specific
    event_start: str = ""
    event_end: str = ""
    event_location: str = ""
    # Job-specific
    job_title: str = ""
    job_company: str = ""
    job_location: str = ""
    job_salary: str = ""
    job_posted: str = ""
    job_expires: str = ""
    # FAQ-specific
    faq_items: List[Dict[str, str]] = field(default_factory=list)
    # Raw JSON-LD data
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ContentClassification:
    """Content type classification result."""
    primary_type: str  # article, product, video, recipe, faq, local_business, event, job, review, unknown
    confidence: float  # 0.0 to 1.0
    secondary_types: List[str] = field(default_factory=list)
    signals: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AnswerExtraction:
    """Extracted answer/snippet data."""
    answer: str = ""
    answer_type: str = ""  # featured_snippet, knowledge_panel, faq, definition, calculation
    source_url: str = ""
    source_title: str = ""
    confidence: float = 0.0


@dataclass
class StructuredResult:
    """Complete structured extraction result."""
    schema_org: SchemaOrgData
    classification: ContentClassification
    answers: List[AnswerExtraction]
    images_with_alt: List[Dict[str, str]]
    microdata: List[Dict[str, Any]]
    rdfa: List[Dict[str, Any]]


# ── JSON-LD Parsing ─────────────────────────────────────────────────────

def _flatten_jsonld(obj: Any, parent_type: str = "") -> List[Dict[str, Any]]:
    """Flatten nested JSON-LD into a list of typed objects."""
    results = []
    if isinstance(obj, list):
        for item in obj:
            results.extend(_flatten_jsonld(item, parent_type))
        return results
    if not isinstance(obj, dict):
        return results

    # Handle @graph
    if "@graph" in obj:
        for item in obj["@graph"]:
            results.extend(_flatten_jsonld(item, parent_type))
        return results

    # Handle @type
    obj_type = obj.get("@type", parent_type)
    if isinstance(obj_type, list):
        obj_type = obj_type[0] if obj_type else parent_type

    results.append(obj)

    # Recurse into nested objects
    for key, value in obj.items():
        if key.startswith("@"):
            continue
        if isinstance(value, dict):
            results.extend(_flatten_jsonld(value, obj_type))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    results.extend(_flatten_jsonld(item, obj_type))

    return results


def _extract_text(value: Any) -> str:
    """Extract text from JSON-LD values (handles strings, objects with @value, lists)."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return value.get("@value", value.get("name", value.get("text", ""))).strip() if isinstance(value.get("@value", value.get("name", value.get("text", ""))), str) else str(value.get("@value", ""))
    if isinstance(value, list):
        parts = [_extract_text(v) for v in value if _extract_text(v)]
        return ", ".join(parts[:5])
    return ""


def parse_jsonld(html: str) -> List[Dict[str, Any]]:
    """Parse all JSON-LD blocks from HTML."""
    tree = HTMLParser(html)
    blocks = []
    for script in tree.css("script[type='application/ld+json']"):
        try:
            data = json.loads(script.text())
            blocks.append(data)
        except Exception:
            continue
    return blocks


def extract_schema_org(html: str, url: str = "") -> SchemaOrgData:
    """Extract Schema.org structured data from HTML via JSON-LD, Microdata, RDFa."""
    result = SchemaOrgData()
    tree = HTMLParser(html)

    # ── JSON-LD ──
    all_blocks = parse_jsonld(html)
    flat = []
    for block in all_blocks:
        flat.extend(_flatten_jsonld(block))

    # Find the most specific type (prefer Product, Recipe, VideoObject, etc.)
    preferred_types = [
        "Product", "Recipe", "VideoObject", "Event", "JobPosting",
        "FAQPage", "LocalBusiness", "Review", "Article", "NewsArticle",
        "BlogPosting", "WebPage",
    ]
    main_obj = None
    for ptype in preferred_types:
        for obj in flat:
            t = obj.get("@type", "")
            if isinstance(t, list):
                t = t[0] if t else ""
            if t == ptype:
                main_obj = obj
                break
        if main_obj:
            break
    if not main_obj and flat:
        main_obj = flat[0]

    if main_obj:
        result.type = _extract_text(main_obj.get("@type", ""))
        result.name = _extract_text(main_obj.get("name", main_obj.get("headline", "")))
        result.description = _extract_text(main_obj.get("description", ""))
        result.url = _extract_text(main_obj.get("url", main_obj.get("mainEntityOfPage", "")))
        if not result.url:
            result.url = url

        # Image
        img = main_obj.get("image", "")
        if isinstance(img, dict):
            img = img.get("url", "")
        elif isinstance(img, list) and img:
            img = img[0] if isinstance(img[0], str) else img[0].get("url", "")
        result.image = _extract_text(img)

        # Dates
        result.date_published = _extract_text(main_obj.get("datePublished", ""))
        result.date_modified = _extract_text(main_obj.get("dateModified", ""))

        # Author
        author = main_obj.get("author", {})
        if isinstance(author, dict):
            result.author = author.get("name", "")
        elif isinstance(author, list) and author:
            result.author = author[0].get("name", "") if isinstance(author[0], dict) else str(author[0])
        elif isinstance(author, str):
            result.author = author

        # Publisher
        pub = main_obj.get("publisher", {})
        if isinstance(pub, dict):
            result.publisher = pub.get("name", "")
        elif isinstance(pub, str):
            result.publisher = pub

        # Product fields
        offers = main_obj.get("offers", {})
        if isinstance(offers, list) and offers:
            offers = offers[0]
        if isinstance(offers, dict):
            result.price = _extract_text(offers.get("price", ""))
            result.price_currency = _extract_text(offers.get("priceCurrency", ""))
            result.availability = _extract_text(offers.get("availability", ""))

        result.brand = _extract_text(main_obj.get("brand", ""))

        # Rating
        agg_rating = main_obj.get("aggregateRating", {})
        if isinstance(agg_rating, dict):
            try:
                result.rating = float(agg_rating.get("ratingValue", 0))
            except (ValueError, TypeError):
                pass
            try:
                result.review_count = int(agg_rating.get("reviewCount", 0))
            except (ValueError, TypeError):
                pass

        # Video fields
        result.duration = _extract_text(main_obj.get("duration", ""))
        result.upload_date = _extract_text(main_obj.get("uploadDate", ""))
        result.embed_url = _extract_text(main_obj.get("embedUrl", ""))

        # Recipe fields
        ingredients = main_obj.get("recipeIngredient", [])
        if isinstance(ingredients, list):
            result.recipe_ingredients = [_extract_text(i) for i in ingredients if _extract_text(i)]
        instructions = main_obj.get("recipeInstructions", [])
        if isinstance(instructions, list):
            result.recipe_instructions = [_extract_text(i) for i in instructions if _extract_text(i)]
        result.recipe_yield = _extract_text(main_obj.get("recipeYield", ""))
        result.recipe_cook_time = _extract_text(main_obj.get("cookTime", ""))
        result.recipe_prep_time = _extract_text(main_obj.get("prepTime", ""))

        # Event fields
        result.event_start = _extract_text(main_obj.get("startDate", ""))
        result.event_end = _extract_text(main_obj.get("endDate", ""))
        loc = main_obj.get("location", {})
        if isinstance(loc, dict):
            result.event_location = loc.get("name", loc.get("address", ""))
        elif isinstance(loc, str):
            result.event_location = loc

        # Job fields
        result.job_title = _extract_text(main_obj.get("title", ""))
        co = main_obj.get("hiringOrganization", {})
        if isinstance(co, dict):
            result.job_company = co.get("name", "")
        elif isinstance(co, str):
            result.job_company = co
        jloc = main_obj.get("jobLocation", {})
        if isinstance(jloc, dict):
            addr = jloc.get("address", {})
            if isinstance(addr, dict):
                result.job_location = f"{addr.get('addressLocality', '')} {addr.get('addressRegion', '')}".strip()
            elif isinstance(addr, str):
                result.job_location = addr
        result.job_salary = _extract_text(main_obj.get("estimatedSalary", ""))
        result.job_posted = _extract_text(main_obj.get("datePosted", ""))
        result.job_expires = _extract_text(main_obj.get("validThrough", ""))

        # FAQ
        if main_obj.get("@type") == "FAQPage":
            entities = main_obj.get("mainEntity", [])
            if isinstance(entities, list):
                for entity in entities:
                    if isinstance(entity, dict):
                        q = _extract_text(entity.get("name", ""))
                        a_list = entity.get("acceptedAnswer", {})
                        if isinstance(a_list, dict):
                            a = _extract_text(a_list.get("text", ""))
                        elif isinstance(a_list, list) and a_list:
                            a = _extract_text(a_list[0].get("text", ""))
                        else:
                            a = ""
                        if q and a:
                            result.faq_items.append({"question": q, "answer": a})

        result.raw = main_obj

    # ── Microdata (itemtype) ──
    microdata = []
    for elem in tree.css("[itemscope]"):
        item_type = elem.attributes.get("itemtype", "")
        if not item_type:
            continue
        props = {}
        for prop in elem.css("[itemprop]"):
            name = prop.attributes.get("itemprop", "")
            content = prop.attributes.get("content", "")
            if not content:
                content = prop.text(strip=True)
            if name and content:
                props[name] = content
        if props:
            microdata.append({"type": item_type, "properties": props})

    result.microdata = microdata  # type: ignore

    return result


# ── Content Classification ──────────────────────────────────────────────

def classify_content(html: str, schema: SchemaOrgData) -> ContentClassification:
    """Classify page content type based on Schema.org data and HTML signals."""
    tree = HTMLParser(html)
    scores: Dict[str, float] = {
        "article": 0.0, "product": 0.0, "video": 0.0,
        "recipe": 0.0, "faq": 0.0, "local_business": 0.0,
        "event": 0.0, "job": 0.0, "review": 0.0,
    }
    signals: Dict[str, Any] = {}

    schema_type = schema.type.replace("https://schema.org/", "").replace("http://schema.org/", "")

    # Score by Schema.org type
    all_signals = {
        "article": _ARTICLE_SIGNALS,
        "product": _PRODUCT_SIGNALS,
        "video": _VIDEO_SIGNALS,
        "recipe": _RECIPE_SIGNALS,
        "faq": _FAQ_SIGNALS,
        "local_business": _LOCAL_BUSINESS_SIGNALS,
        "event": _EVENT_SIGNALS,
        "job": _JOB_SIGNALS,
        "review": _REVIEW_SIGNALS,
    }

    for content_type, s in all_signals.items():
        if schema_type in s["schema_types"]:
            scores[content_type] += 5.0
            signals[f"{content_type}_schema"] = schema_type

    # Score by meta tags
    meta_tags = {}
    for node in tree.css("meta[property], meta[name]"):
        prop = node.attributes.get("property") or node.attributes.get("name", "")
        content = node.attributes.get("content", "")
        if prop and content:
            meta_tags[prop] = content

    for content_type, s in all_signals.items():
        for tag in s["meta_tags"]:
            if tag in meta_tags:
                scores[content_type] += 2.0
                signals[f"{content_type}_meta"] = tag

    # Score by HTML signals
    for content_type, s in all_signals.items():
        for selector in s["html_signals"]:
            try:
                matches = tree.css(selector)
                if matches:
                    scores[content_type] += 1.0 * min(len(matches), 3)
                    signals[f"{content_type}_html"] = selector
            except Exception:
                continue

    # Additional heuristics
    if schema.faq_items:
        scores["faq"] += 3.0
    if schema.recipe_ingredients:
        scores["recipe"] += 3.0
    if schema.price:
        scores["product"] += 3.0
    if schema.duration:
        scores["video"] += 2.0
    if schema.event_start:
        scores["event"] += 3.0
    if schema.job_title:
        scores["job"] += 3.0

    # Determine primary type
    max_score = max(scores.values()) if scores else 0
    if max_score < 1.0:
        primary = "unknown"
        confidence = 0.0
    else:
        primary = max(scores, key=scores.get)  # type: ignore
        confidence = min(max_score / 10.0, 1.0)

    # Secondary types (above threshold)
    secondary = [t for t, s in scores.items()
                 if s >= 2.0 and t != primary]

    return ContentClassification(
        primary_type=primary,
        confidence=round(confidence, 2),
        secondary_types=secondary,
        signals=signals,
    )


# ── Answer Extraction ───────────────────────────────────────────────────

def extract_answers(html: str, url: str = "") -> List[AnswerExtraction]:
    """Extract featured snippets, knowledge panels, FAQs, and definitions."""
    tree = HTMLParser(html)
    answers: List[AnswerExtraction] = []

    # ── Featured Snippets ──
    # Google featured snippets are in div.kb-responsive or similar
    snippet_selectors = [
        ".kp-wholepage",  # Knowledge panel
        ".xpdopen",  # Featured snippet container
        ".LGOjhe",  # Answer box
        ".IZ6rdc",  # Featured snippet text
        "[data-attrid='wa:/description']",  # Knowledge panel description
        ".kno-rdesc",  # Knowledge panel short description
        ".BNeawe",  # Direct answer
        ".IsZvec",  # Answer snippet
        "[data-header-feature]",  # Header feature answer
    ]
    for sel in snippet_selectors:
        try:
            node = tree.css_first(sel)
            if node:
                text = node.text(strip=True)
                if text and len(text) > 20:
                    answers.append(AnswerExtraction(
                        answer=text[:500],
                        answer_type="featured_snippet",
                        source_url=url,
                        confidence=0.8,
                    ))
                    break
        except Exception:
            continue

    # ── Knowledge Panel ──
    kp_selectors = [".kp-blk", ".rc[data-attrid]", ".LGOjhe", ".kp-wholepage"]
    for sel in kp_selectors:
        try:
            node = tree.css_first(sel)
            if node:
                title = ""
                desc = ""
                title_node = node.css_first("h2, h3, .djdyq")
                if title_node:
                    title = title_node.text(strip=True)
                desc_node = node.css_first(".kno-rdesc span, .LGOjhe, [data-attrid='wa:/description']")
                if desc_node:
                    desc = desc_node.text(strip=True)
                if title or desc:
                    answers.append(AnswerExtraction(
                        answer=f"{title}: {desc}" if title and desc else title or desc,
                        answer_type="knowledge_panel",
                        source_url=url,
                        confidence=0.7,
                    ))
                    break
        except Exception:
            continue

    # ── FAQ Extraction ──
    faq_selectors = [
        ".related-question-pair",  # Google PAA (People Also Ask)
        ".wDYxhc",  # PAA container
        "[data-q]",
        "details",
        ".faq-item", ".faq-question", ".accordion-item",
    ]
    for sel in faq_selectors:
        try:
            nodes = tree.css(sel)
            for node in nodes[:10]:
                q_text = ""
                a_text = ""
                if sel == "details":
                    summary = node.css_first("summary")
                    q_text = summary.text(strip=True) if summary else ""
                    a_text = node.text(strip=True)
                    if q_text and a_text:
                        a_text = a_text.replace(q_text, "", 1).strip()
                else:
                    q_node = node.css_first("[data-q], .question, h3, h4, strong")
                    a_node = node.css_first(".wDYxhc, .answer, .lEBKkf, p, span")
                    q_text = q_node.text(strip=True) if q_node else ""
                    a_text = a_node.text(strip=True) if a_node else ""
                if q_text and a_text:
                    answers.append(AnswerExtraction(
                        answer=f"Q: {q_text}\nA: {a_text}",
                        answer_type="faq",
                        source_url=url,
                        confidence=0.9,
                    ))
        except Exception:
            continue

    # ── Definition / Calculation ──
    def_selectors = [
        ".d4chem",  # Google dictionary definition
        ".tfkVOd",  # Calculator result
        ".BNeawe.s3v9rd",  # Direct answer
        ".IZ6rdc",  # Definition box
    ]
    for sel in def_selectors:
        try:
            node = tree.css_first(sel)
            if node:
                text = node.text(strip=True)
                if text and len(text) > 5:
                    atype = "calculation" if "tfkVOd" in sel else "definition"
                    answers.append(AnswerExtraction(
                        answer=text[:300],
                        answer_type=atype,
                        source_url=url,
                        confidence=0.85,
                    ))
                    break
        except Exception:
            continue

    return answers[:20]  # cap at 20


# ── Image Alt-Text Extraction ───────────────────────────────────────────

def extract_images_with_alt(html: str, url: str = "",
                            max_images: int = 50) -> List[Dict[str, str]]:
    """Extract images with their alt text, title, and context."""
    tree = HTMLParser(html)
    images: List[Dict[str, str]] = []
    seen = set()

    for img in tree.css("img[src]"):
        src = (img.attributes or {}).get("src", "").strip()
        if not src or src.startswith("data:") or len(src) < 5:
            continue
        full_url = urljoin(url, src) if url else src
        if full_url in seen:
            continue
        seen.add(full_url)

        attrs = img.attributes or {}
        alt = attrs.get("alt", "") or ""
        if hasattr(alt, "strip"):
            alt = alt.strip()
        else:
            alt = str(alt).strip() if alt else ""
        title = attrs.get("title", "") or ""
        if hasattr(title, "strip"):
            title = title.strip()
        else:
            title = str(title).strip() if title else ""
        width = attrs.get("width", "")
        height = attrs.get("height", "")

        # Get parent context (caption text)
        context = ""
        parent = img.parent
        if parent and parent.tag in ("figure", "picture", "div"):
            caption = parent.css_first("figcaption, .caption, .img-caption, span")
            if caption:
                context = caption.text(strip=True)

        # Skip tiny tracking pixels / icons
        if width and height:
            try:
                w, h = int(width), int(height)
                if w < 20 or h < 20:
                    continue
            except ValueError:
                pass

        entry = {"url": full_url, "alt": alt, "title": title}
        if context:
            entry["context"] = context
        if width:
            entry["width"] = str(width)
        if height:
            entry["height"] = str(height)
        images.append(entry)

        if len(images) >= max_images:
            break

    return images


# ── Microdata Parsing ───────────────────────────────────────────────────

def parse_microdata(html: str) -> List[Dict[str, Any]]:
    """Extract Microdata (itemscope/itemprop) from HTML."""
    tree = HTMLParser(html)
    items = []

    for elem in tree.css("[itemscope]"):
        item_type = elem.attributes.get("itemtype", "")
        props: Dict[str, Any] = {}

        for prop_elem in elem.css("[itemprop]"):
            name = prop_elem.attributes.get("itemprop", "")
            if not name:
                continue

            content = prop_elem.attributes.get("content", "")
            if not content:
                href = prop_elem.attributes.get("href", "")
                src = prop_elem.attributes.get("src", "")
                if href:
                    content = href
                elif src:
                    content = src
                else:
                    content = prop_elem.text(strip=True)

            if name in props:
                if isinstance(props[name], list):
                    props[name].append(content)
                else:
                    props[name] = [props[name], content]
            else:
                props[name] = content

        if props:
            items.append({"type": item_type, "properties": props})

    return items


# ── RDFa Parsing ────────────────────────────────────────────────────────

def parse_rdfa(html: str) -> List[Dict[str, Any]]:
    """Extract RDFa (vocab, typeof, property, resource) from HTML."""
    tree = HTMLParser(html)
    items = []

    for elem in tree.css("[typeof]"):
        typeof = elem.attributes.get("typeof", "")
        vocab = elem.attributes.get("vocab", "")
        props: Dict[str, str] = {}

        # Get vocab from parent or attribute
        parent = elem.parent
        if not vocab and parent:
            vocab = parent.attributes.get("vocab", "")

        for prop_elem in elem.css("[property]"):
            name = prop_elem.attributes.get("property", "")
            if not name:
                continue
            content = prop_elem.attributes.get("content", "")
            if not content:
                content = prop_elem.text(strip=True)
            props[name] = content

        if props or typeof:
            items.append({"type": typeof, "vocab": vocab, "properties": props})

    return items


# ── Unified Extraction ──────────────────────────────────────────────────

def extract_structured(html: str, url: str = "") -> StructuredResult:
    """Run all structured extraction on HTML and return a unified result."""
    schema = extract_schema_org(html, url)
    classification = classify_content(html, schema)
    answers = extract_answers(html, url)
    images = extract_images_with_alt(html, url)
    microdata = parse_microdata(html)
    rdfa = parse_rdfa(html)

    return StructuredResult(
        schema_org=schema,
        classification=classification,
        answers=answers,
        images_with_alt=images,
        microdata=microdata,
        rdfa=rdfa,
    )
