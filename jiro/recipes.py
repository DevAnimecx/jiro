"""Custom extraction recipes — CSS, XPath and JSONPath (PRD §6.2 / Phase 3).

A recipe maps output field names to extraction rules::

    {
      "css":     {"title": "h1", "links": "a[href]@href", "prices": ".price | price"},
      "xpath":   {"author": "//meta[@name='author']/@content"},
      "jsonpath": {"items": "$.results[*].name"},
    }

Rule grammar
------------
* ``selector``            → element text (CSS) / text (XPath)
* ``selector@attr``        → attribute value (CSS)
* ``selector | json``      → parse JSON-LD / <script type=application/ld+json>
* ``selector | markdown``  → convert element HTML to markdown
* ``selector | count``     → number of matching elements

CSS is evaluated with selectolax, XPath with lxml (optional dependency),
JSONPath with a small built-in evaluator (no external dependency).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from jiro.errors import JiroError


class RecipeError(JiroError):
    code = "recipe_error"
    status_code = 422


# --------------------------------------------------------------------------
# Minimal JSONPath evaluator (subset: $, .field, [n], [*], [a,b], filters)
# --------------------------------------------------------------------------
_TOKEN_RE = re.compile(
    r"(\$|\.\.|@|\.\w+|\['[^']*'\]|\"\[[^\]]*\]\"|\[[^]]*\]|\*|\?\()"
)


def _tokenize(path: str) -> List[str]:
    tokens: List[str] = []
    i = 0
    while i < len(path):
        ch = path[i]
        if ch == "$":
            tokens.append("$")
            i += 1
        elif ch == ".":
            if i + 1 < len(path) and path[i + 1] == ".":
                tokens.append("..")
                i += 2
            else:
                m = re.match(r"\.([A-Za-z_][A-Za-z0-9_]*)", path[i:])
                if m:
                    tokens.append(m.group(0))
                    i += m.end()
                else:
                    i += 1
        elif ch == "[":
            m = re.match(r"\[([^]]*)\]", path[i:])
            if m:
                tokens.append(m.group(0))
                i += m.end()
            else:
                i += 1
        elif ch == "'":
            m = re.match(r"'([^']*)'", path[i:])
            if m:
                tokens.append(m.group(1))
                i += m.end()
            else:
                i += 1
        elif ch == "*":
            tokens.append("*")
            i += 1
        elif ch == "@":
            tokens.append("@")
            i += 1
        else:
            m = re.match(r"[A-Za-z_][A-Za-z0-9_]*", path[i:])
            if m:
                tokens.append(m.group(0))
                i += m.end()
            else:
                i += 1
    return tokens


def jsonpath_eval(data: Any, path: str) -> List[Any]:
    """Evaluate a JSONPath subset against parsed JSON data."""
    tokens = _tokenize(path)
    results: List[Any] = [data]
    idx = 0

    while idx < len(tokens) and results:
        tok = tokens[idx]
        if tok == "$":
            idx += 1
            continue
        if tok == "*":
            results = _star(results)
            idx += 1
            continue
        if tok == "..":
            # descendant search for the next key token
            if idx + 1 < len(tokens):
                nxt = tokens[idx + 1]
                if nxt.startswith("."):
                    key = nxt[1:]
                elif nxt.startswith("[") and nxt[1:2] in ("'", '"'):
                    key = nxt[2:-2]
                else:
                    key = nxt.strip("[]'\"")
                found = []
                for r in results:
                    found.extend(_descendant(r, key))
                results = found
                idx += 2
                continue
            idx += 1
            continue
        if tok.startswith("."):
            key = tok[1:]
            results = [r[key] for r in results if isinstance(r, dict) and key in r]
            idx += 1
            continue
        if tok.startswith("["):
            body = tok[1:-1].strip()
            if body in ("*", ""):
                results = _star(results)
            elif body.startswith(("'", '"')) and body.endswith(("'", '"')):
                key = body[1:-1]
                results = [r[key] for r in results if isinstance(r, dict) and key in r]
            elif "?" in body:
                results = _filter(results, body)
            else:
                results = _index(results, body)
            idx += 1
            continue
        # bare identifier
        results = [r[tok] for r in results if isinstance(r, dict) and tok in r]
        idx += 1

    return results


def _star(items: List[Any]) -> List[Any]:
    out: List[Any] = []
    for item in items:
        if isinstance(item, list):
            out.extend(item)
        elif isinstance(item, dict):
            out.extend(item.values())
    return out


def _descendant(item: Any, key: str) -> List[Any]:
    out: List[Any] = []
    if isinstance(item, dict):
        for k, v in item.items():
            if k == key:
                out.append(v)
            out.extend(_descendant(v, key))
    elif isinstance(item, list):
        for v in item:
            out.extend(_descendant(v, key))
    return out


def _index(items: List[Any], body: str) -> List[Any]:
    out: List[Any] = []
    for part in body.split(","):
        part = part.strip()
        if ":" in part:
            bits = part.split(":")
            start = int(bits[0]) if bits[0] else 0
            stop = int(bits[1]) if len(bits) > 1 and bits[1] else None
            for item in items:
                if isinstance(item, list):
                    out.extend(item[start:stop])
        else:
            n = int(part)
            for item in items:
                if isinstance(item, list) and -len(item) <= n < len(item):
                    out.append(item[n])
    return out


def _filter(items: List[Any], body: str) -> List[Any]:
    # very small subset: [?(key op value)] e.g. [?(@.price > 10)]
    m = re.search(r"@\.([A-Za-z_]\w*)\s*(==|!=|>=|<=|>|<)\s*([^\]\s]+)", body)
    if not m:
        return items
    key, op, val_s = m.group(1), m.group(2), m.group(3).strip("'\"")
    try:
        val: Any = float(val_s) if re.fullmatch(r"-?\d+(\.\d+)?", val_s) else val_s
    except ValueError:
        val = val_s
    out: List[Any] = []
    for item in items:
        if not isinstance(item, dict) or key not in item:
            continue
        cur = item[key]
        try:
            if op == "==" and cur == val:
                out.append(item)
            elif op == "!=" and cur != val:
                out.append(item)
            elif op in (">", "<", ">=", "<=") and isinstance(cur, (int, float)):
                if op == ">" and cur > val:
                    out.append(item)
                elif op == "<" and cur < val:
                    out.append(item)
                elif op == ">=" and cur >= val:
                    out.append(item)
                elif op == "<=" and cur <= val:
                    out.append(item)
        except TypeError:
            continue
    return out


# --------------------------------------------------------------------------
# Recipe engine
# --------------------------------------------------------------------------
def apply_recipe(html: str, recipe: Dict[str, Any], *, url: str = "") -> Dict[str, Any]:
    """Apply a {css, xpath, jsonpath} recipe to page HTML."""
    if not isinstance(recipe, dict) or not any(
        k in recipe for k in ("css", "xpath", "jsonpath")
    ):
        raise RecipeError("recipe must contain at least one of: css, xpath, jsonpath")

    result: Dict[str, Any] = {}
    for kind in ("css", "xpath", "jsonpath"):
        rules = recipe.get(kind)
        if not rules:
            continue
        if not isinstance(rules, dict):
            raise RecipeError(f"recipe['{kind}'] must be a dict of field -> rule")
        for field, rule in rules.items():
            if not isinstance(rule, str):
                raise RecipeError(f"rule for '{field}' must be a string")
            result[field] = _extract_rule(html, kind, rule, url=url)
    return result


def _extract_rule(html: str, kind: str, rule: str, *, url: str) -> Any:
    selector, _, modifier = rule.partition(" | ")
    selector = selector.strip()
    modifier = modifier.strip().lower() if modifier else ""

    if kind == "css":
        raw = _css_values(html, selector, url=url)
    elif kind == "xpath":
        raw = _xpath_values(html, selector)
    elif kind == "jsonpath":
        data = _extract_json_ld(html)
        raw = jsonpath_eval(data, selector) if data else []
    else:  # pragma: no cover
        raw = []

    if not raw:
        return None

    if modifier == "json":
        return [item for item in raw if isinstance(item, (dict, list))]
    if modifier == "count":
        return len(raw)
    if modifier == "markdown":
        from jiro.extract import ContentExtractor
        tree = ContentExtractor(html, url=url).tree
        out = []
        for node in tree.css(selector):
            from jiro.extract import node_to_markdown
            md = node_to_markdown(node, base_url=url)
            if md.strip():
                out.append(md)
        return out[0] if len(out) == 1 else out
    # default: text / attribute already normalized
    return raw[0] if len(raw) == 1 else raw


def _css_values(html: str, selector: str, *, url: str) -> List[Any]:
    from selectolax.parser import HTMLParser

    tree = HTMLParser(html)
    # attribute extraction: selector@attr
    if "@" in selector and not selector.endswith(")"):
        css, _, attr = selector.rpartition("@")
        css, attr = css.strip(), attr.strip()
        if not attr:
            return []
        nodes = tree.css(css)
        if attr == "text":
            return [n.text(strip=True) for n in nodes if n.text(strip=True)]
        return [n.attributes.get(attr) for n in nodes if n.attributes.get(attr)]
    nodes = tree.css(selector)
    return [n.text(strip=True) for n in nodes if n.text(strip=True)]


def _xpath_values(html: str, xpath: str) -> List[Any]:
    from lxml import etree, html as lxml_html

    doc = lxml_html.fromstring(html.encode("utf-8", "ignore"))
    try:
        found = doc.xpath(xpath)
    except (etree.XPathError, ValueError) as exc:
        raise RecipeError(f"invalid xpath '{xpath}': {exc}") from exc
    out: List[Any] = []
    for item in found:
        if isinstance(item, str):
            text = item.strip()
            if text:
                out.append(text)
        else:  # element
            text = item.text_content().strip() if hasattr(item, "text_content") else str(item)
            if text:
                out.append(text)
    return out


def _extract_json_ld(html: str) -> Any:
    import json as _json
    from selectolax.parser import HTMLParser

    tree = HTMLParser(html)
    data: Any = None
    for script in tree.css("script[type='application/ld+json']"):
        try:
            parsed = _json.loads(script.text())
        except Exception:
            continue
        if isinstance(parsed, list):
            data = (data or []) + parsed
        else:
            data = parsed
    return data or {}
