"""Export formats — JSON, CSV, XML, RSS output for search and scrape results.

Converts SearchResponse and ScrapeResponse data into various wire formats
for integration with external tools and pipelines.
"""

from __future__ import annotations

import csv
import io
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from xml.dom import minidom


def _escape_xml(text: str) -> str:
    """Escape special XML characters."""
    if not text:
        return ""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;"))


def _safe_text(val: Any) -> str:
    """Convert any value to safe text for XML/CSV."""
    if val is None:
        return ""
    s = str(val)
    return s.replace("\n", " ").replace("\r", "").strip()


def _parse_date(date_str: str) -> Optional[datetime]:
    """Try to parse a date string into a datetime."""
    if not date_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%d", "%Y-%m-%dT%H:%M", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None


# ── JSON Export ──────────────────────────────────────────────────────────

def to_json(data: Dict[str, Any], *, pretty: bool = False,
            indent: int = 2) -> str:
    """Export search/scrape result as JSON string."""
    return json.dumps(data, indent=indent if pretty else None,
                      default=str, ensure_ascii=False)


# ── CSV Export ───────────────────────────────────────────────────────────

def to_csv(results: List[Dict[str, Any]], *,
           fields: Optional[List[str]] = None) -> str:
    """Export organic results as CSV. Auto-detects fields if not specified."""
    if not results:
        return ""

    if fields is None:
        fields = ["position", "title", "link", "snippet", "source", "date",
                  "price", "rating", "reviews", "channel", "views"]
        # Add any extra fields from results
        for r in results:
            for k in r:
                if k not in fields and k not in ("sitelinks", "rich_snippet",
                                                  "thumbnail", "raw"):
                    fields.append(k)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore",
                            quoting=csv.QUOTE_MINIMAL)
    writer.writeheader()
    for r in results:
        row = {}
        for f in fields:
            val = r.get(f, "")
            if isinstance(val, (dict, list)):
                val = json.dumps(val, default=str)
            row[f] = _safe_text(val)
        writer.writerow(row)
    return output.getvalue()


# ── XML Export ───────────────────────────────────────────────────────────

def to_xml(data: Dict[str, Any], *, root_tag: str = "search_results",
           item_tag: str = "result") -> str:
    """Export search results as XML."""
    root = ET.Element(root_tag)

    # Metadata
    meta = data.get("search_metadata", {})
    if meta:
        meta_elem = ET.SubElement(root, "metadata")
        for k, v in meta.items():
            child = ET.SubElement(meta_elem, _escape_xml(k))
            child.text = _safe_text(v)

    # Search info
    info = data.get("search_information", {})
    if info:
        info_elem = ET.SubElement(root, "search_information")
        for k, v in info.items():
            child = ET.SubElement(info_elem, _escape_xml(k))
            child.text = _safe_text(v)

    # Organic results
    results = data.get("organic_results", [])
    results_elem = ET.SubElement(root, "results")
    results_elem.set("count", str(len(results)))
    for r in results:
        item = ET.SubElement(results_elem, item_tag)
        item.set("position", str(r.get("position", "")))
        for key, val in r.items():
            if key in ("sitelinks", "rich_snippet", "raw"):
                continue
            if isinstance(val, (dict, list)):
                continue
            child = ET.SubElement(item, _escape_xml(key))
            child.text = _safe_text(val)

    # Related searches
    related = data.get("related_searches", [])
    if related:
        rel_elem = ET.SubElement(root, "related_searches")
        for r in related:
            child = ET.SubElement(rel_elem, "query")
            if isinstance(r, dict):
                child.text = _safe_text(r.get("query", ""))
            else:
                child.text = _safe_text(r)

    # Knowledge graph
    kg = data.get("knowledge_graph", {})
    if kg:
        kg_elem = ET.SubElement(root, "knowledge_graph")
        for k, v in kg.items():
            if isinstance(v, str):
                child = ET.SubElement(kg_elem, _escape_xml(k))
                child.text = _safe_text(v)

    # Pretty print
    rough = ET.tostring(root, encoding="unicode", xml_declaration=False)
    parsed = minidom.parseString(rough)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + parsed.toprettyxml(indent="  ")[23:]


# ── RSS Export ───────────────────────────────────────────────────────────

def to_rss(data: Dict[str, Any], *, feed_title: str = "Jiro Search Results",
           feed_link: str = "", feed_description: str = "",
           feed_language: str = "en-us") -> str:
    """Export search results as RSS 2.0 feed."""
    rss = ET.Element("rss")
    rss.set("version", "2.0")
    rss.set("xmlns:atom", "http://www.w3.org/2005/Atom")
    rss.set("xmlns:content", "http://purl.org/rss/1.0/modules/content/")

    channel = ET.SubElement(rss, "channel")

    # Channel metadata
    q = data.get("search_metadata", {}).get("query", "")
    ET.SubElement(channel, "title").text = feed_title or f"Search: {q}"
    ET.SubElement(channel, "link").text = feed_link or "https://jiro.local"
    ET.SubElement(channel, "description").text = (
        feed_description or f"Search results for: {q}"
    )
    ET.SubElement(channel, "language").text = feed_language
    ET.SubElement(channel, "generator").text = "Jiro Search API v1"
    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    ET.SubElement(channel, "lastBuildDate").text = now

    # Atom self link
    atom_link = ET.SubElement(channel, "atom:link")
    atom_link.set("href", feed_link or "https://jiro.local/search")
    atom_link.set("rel", "self")
    atom_link.set("type", "application/rss+xml")

    # Items from organic results
    results = data.get("organic_results", [])
    for r in results:
        item = ET.SubElement(channel, "item")
        title = r.get("title", "Untitled")
        link = r.get("link", "")
        snippet = r.get("snippet", "")

        ET.SubElement(item, "title").text = _safe_text(title)
        ET.SubElement(item, "link").text = link
        ET.SubElement(item, "description").text = _safe_text(snippet)

        # GUID
        guid = ET.SubElement(item, "guid")
        guid.text = link or title
        guid.set("isPermaLink", "true" if link else "false")

        # Pub date
        pub_date = r.get("date", "")
        dt = _parse_date(pub_date)
        if dt:
            ET.SubElement(item, "pubDate").text = dt.strftime(
                "%a, %d %b %Y %H:%M:%S +0000"
            )

        # Source
        source = r.get("source", "")
        if source:
            ET.SubElement(item, "source").text = _safe_text(source)

        # Content encoded (full snippet)
        if snippet:
            content = ET.SubElement(item, "content:encoded")
            content.text = snippet

    # Related searches as items
    related = data.get("related_searches", [])
    for r in related[:5]:
        item = ET.SubElement(channel, "item")
        query = r.get("query", "") if isinstance(r, dict) else str(r)
        ET.SubElement(item, "title").text = f"Related: {query}"
        ET.SubElement(item, "description").text = f"Related search query: {query}"

    rough = ET.tostring(rss, encoding="unicode", xml_declaration=False)
    parsed = minidom.parseString(rough)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + parsed.toprettyxml(indent="  ")[23:]


# ── Format Router ────────────────────────────────────────────────────────

def export(data: Dict[str, Any], fmt: str, **kwargs: Any) -> str:
    """Export data in the specified format.

    Supported formats: json, csv, xml, rss
    """
    fmt = fmt.strip().lower()
    if fmt == "json":
        return to_json(data, **kwargs)
    if fmt == "csv":
        results = data.get("organic_results", [])
        return to_csv(results, **kwargs)
    if fmt == "xml":
        return to_xml(data, **kwargs)
    if fmt == "rss":
        return to_rss(data, **kwargs)
    raise ValueError(f"unsupported export format: {fmt}")
