## What's New in 0.3.3

### Results That Match Your Request
Engines return about one page of results regardless of `num`. `num` above that page size used to be
ignored, so a request for 50 results silently came back with 3. Jiro now walks successive SERP pages
on the same engine until the count is met, the engine runs dry, or the page budget is used up.
Results are deduped by link and capped back to `num`.

Pagination is opt-in per engine via `BaseEngine.supports_pagination`, enabled for Google (`start`),
Bing (`first`), and Baidu (`pn`) — the engines whose upstream request actually accepts an offset.
Paging an engine that ignores the offset just re-fetches the same rows, so it is not attempted.

### No More Silent Short Pages
When fewer results than requested are available, Jiro says so instead of letting you assume a short
page is the complete answer:

```json
"search_metadata": {
  "requested_num": 50,
  "truncated": true,
  "pages_fetched": 2,
  "pagination_supported": true
}
```

### You Always Know Which Engine Answered
If a fallback engine served your request, `search_metadata` now records both:

```json
"search_metadata": {
  "engine_requested": "google",
  "engine": "bing",
  "engine_substituted": true
}
```

This is set on fresh results, exact cache hits, and semantic cache hits alike.

### Health Checks That Actually Check
`health_check()` defaults to live mode: each engine is probed with a real request, so rate limits,
CAPTCHA walls, and parse regressions show up as failures. Previously every engine reported healthy
because its module merely imported. Pass `live=False` for the cheap instantiation-only check.

```python
orchestrator.probe_engine("google")   # single engine, no fallback chain
await orchestrator.health_check()     # live probes, all engines
```

### Expanded MCP Tool Surface
MCP tools gained structured input validation — bad engines, result types, scrape formats, oversized
URLs, and malformed schemas are rejected up front with actionable messages instead of surfacing as
internal errors.

### Unified 0.3.3 Packages
CLI, OpenAPI spec, npm package, and the Python, JavaScript, and Go SDKs are aligned on `0.3.3`.

## Bug Fixes
- Fixed `jiro ai` failing on the removed Starlette `TestClient(timeout=...)` kwarg
- Fixed `_heal_file_lock()` shadowing the module-level `os` import
- Fixed device authorization reporting a licence failure as an internal error

## Install
```bash
pip install jirosearch==0.3.3
```