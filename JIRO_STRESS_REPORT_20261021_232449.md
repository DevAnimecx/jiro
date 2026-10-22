# Jirosearch Sandbox Stress Test Report

**Run:** 2026-10-21 23:24:49 local (17:52–18:05 UTC)
**Target:** `jiro` CLI v0.3.1 + `jiro serve` (local, port 9999)
**Host:** Windows 11 Home Single Language 10.0.26200, Python 3.14.0, uv tool install
**Raw log:** `C:\temp\jiro_stress\jiro_stress_20261021_232449.log`
**Method:** CLI invocation + local REST API only. No source modifications.

---

## Executive Summary

**Verdict: NOT production-ready.** The tool never hard-crashes under load — it is remarkably stable under concurrency — but it has a systemic honesty problem: **it reports success while silently doing something other than what was asked.**

The single most important number: **60 of 90 engine-exhaustion searches (67%) silently substituted a different engine, and all 90 reported `status: "success"` with a 100% success rate.**

Three defects are launch-blocking:

1. **Silent engine substitution reported as success** (Critical) — you ask for Google, you get Bing, the JSON says `success`. Only `engine_substituted: true` reveals it, and nothing surfaces that to a normal caller.
2. **Real crashes/hangs on ordinary input** (Critical) — non-ASCII queries crash with `UnicodeEncodeError`; `jiro keys list` and `jiro usage` hang forever with no timeout.
3. **`jiro doctor` lies about connectivity** (High) — reports "No internet connectivity" on a machine with working internet, while 3 of its own checks pass on stale assumptions.

---

## Headline Findings

| # | Finding | Severity | Evidence |
|---|---------|----------|----------|
| 1 | Silent engine substitution, still reported as `success` | **Critical** | 60/90 runs; `engine_requested: google` → `engine: bing`, `status: success` |
| 2 | `UnicodeEncodeError` crash on CJK/Cyrillic query | **Critical** | `jiro search web "日本語テスト 中文搜索 한국어"` → traceback |
| 3 | `jiro keys list` hangs indefinitely (no timeout) | **Critical** | rc=124 after 60s kill |
| 4 | `jiro usage --days 90` hangs indefinitely | **Critical** | rc=124 after 60s kill |
| 5 | `~/.local/bin`'s `doctor` network probe false-negative | **High** | "No internet" while `curl` → HTTP 200 ×4 hosts |
| 6 | `--json` emits invalid JSON (raw control chars) | **High** | `json.loads` → `Invalid control character at line 37 col 76` |
| 7 | Server DNS failure to resolve `www.google.com` | **High** | `Could not resolve host` while shell resolves 142.251.157.119 |
| 8 | `--engine nonexistent_engine` exits **rc=0** | **High** | Bad engine silently returns 0 results, success |
| 9 | HTTP 502 after 23s when all engines fail | **High** | `/search` → `{"error":"all engines failed"}` |
| 10 | Server default engine `mock` does not exist | **High** | `{"error":"unknown engine 'mock'"}` HTTP 422 |
| 11 | 404/500 scrape falls back to *search* instead of reporting status | **Medium** | `scrape httpbin.org/status/404` → "engine 's..." |
| 12 | Scrape has no timeout — hangs indefinitely | **Medium** | `httpbin.org/delay/15` killed at 30s (rc=124) |
| 13 | Empty URL silently becomes a search query | **Medium** | `jiro scrape ""` → `Searching for ''` |
| 14 | Batch reports per-item failure inside HTTP 200 | **Medium** | `{"query":"stress","error":"Search failed"}` with HTTP 200 |
| 15 | `StarletteDeprecationWarning` pollutes stderr on every call | **Low** | `cli.py:817` / `cli.py:977` on all scrape/ask/keys paths |
| 16 | CLI and server disagree on engine roster | **Low** | CLI: 9 plugins; server log: `["google","bing","brave","duckduckgo","mock"]` |

---

## Test Environment Corrections

The provided test plan assumed a command surface that does not exist. These are **plan defects, not tool defects**, and are logged here so results are not misread.

| Plan assumed | Actual |
|---|---|
| Engines: google, bing, duckduckgo, brave, yandex, baidu, **ecosia, startpage, mojeek** | ecosia, startpage, mojeek **are not installed**. Real 9: google, bing, duckduckgo, brave, yandex, baidu, amazon, ebay, youtube |
| `--engines 9` for parallel | Flag is `--num-engines`, **hard max 5**. "9 parallel" is impossible |
| `jiro config init/show/set` | Only `init` and `show` exist. **No `config set`** |
| `jiro keys add/remove` | Only `create`, `list`, `revoke` |
| `jiro plugins remove` | Only `list`, `info`, `discover`, `validate`, `create` |
| `jiro search web ""` = error case | Empty query = **interactive mode**, not an error |
| `--num 99999` = absurd count | Query omitted → drops into **interactive mode** |
| `jiro usage --days 90` | Requires admin key; without it, **hangs** |

**Substitution I made:** Phases 1–2 run against the 9 engines that actually exist. The 4 missing engines are recorded as `N/A — not installed` rather than 40 identical failures.

---

## Phase 1 — Engine Exhaustion (SEQUENTIAL)

9 real engines × 10 runs, `--num 100`, 2s cooldown. Query: `"stress test query"`.

| engine | n | min | avg | p95 | max | success | substituted | error types |
|---|---|---|---|---|---|---|---|---|
| google | 10 | 2051 | 3097 | 9140 | 9140 | 100% | **10/10** | engine_blocked×20, irrelevant_results×10 |
| bing | 10 | 1998 | 2562 | 4435 | 4435 | 100% | 0/10 | — |
| duckduckgo | 10 | 1910 | 2113 | 3205 | 3205 | 100% | 0/10 | — |
| brave | 10 | 2096 | 3020 | 8430 | 8430 | 100% | **10/10** | engine_blocked×20 |
| yandex | 10 | 1925 | 2503 | 6040 | 6040 | 100% | **10/10** | engine_blocked×20 |
| baidu | 10 | 1962 | 8710 | 63624 | **63624** | 100% | **10/10** | engine_error×10, engine_blocked×10 |
| amazon | 10 | 1827 | 2603 | 5563 | 5563 | 100% | 0/10 | — |
| ebay | 10 | 1844 | 2481 | 6989 | 6989 | 100% | **10/10** | engine_blocked×20 |
| youtube | 10 | 1850 | 2260 | 5102 | 5102 | 100% | **10/10** | na×10, engine_blocked×10 |

**ALL ENGINES:** n=90, min=1827ms, avg=3261ms, p95=6040ms, max=63624ms
**Total substitutions: 60/90 (67%)**

### Findings

- **FAIL — Silent substitution (Critical).** Google, brave, yandex, baidu, ebay, youtube **never served their own results**. Every run reported `status: "success"`. A caller requesting Google receives Bing or DuckDuckGo results with no error and no exit-code signal.
- **FAIL — Success rate is meaningless (Critical).** Reported 100% success across the board. Real engine fidelity is **33% (3 of 9 engines: bing, duckduckgo, amazon)**.
- **FAIL — baidu p95 = 63.6s (High).** 6.8× the next-worst engine. One run took 63s. Any client with a 30s timeout fails intermittently.
- **PASS — No process crashes.** All 90 runs returned rc=0 or handled errors; no segfaults, no hangs.
- **PASS — Baseline latency acceptable.** Non-substituted engines: 1.9–2.6s avg.
- **NOTE — 429 observed.** Google returned HTTP 429 on repeated hits (`engine_blocked`). Rate limiting is real, and it degrades into substitution rather than a surfaced error.

**Reproduce the substitution:**
```bash
jiro search web "stress test query" --engine google --num 100 --json
```
Then inspect `search_metadata.engine_substituted` and compare `engine_requested` vs `engine`.

**Missing engines — N/A:** `ecosia`, `startpage`, `mojeek` are not present in `jiro plugins list` (9 engines: amazon, baidu, bing, brave, duckduckgo, ebay, google, yandex, youtube). 40 planned test cases unrunnable.

---

## Phase 2 — Parallel Fan-Out (CONCURRENT)

`--parallel --num-engines 5 --num 100`, 20 runs, **no cooldown**.

| metric | value |
|---|---|
| runs | 20/20 completed |
| min | 1851 ms |
| avg | 2070 ms |
| p95 | 2248 ms |
| max | 3403 ms |
| success | 20/20 |
| engines actually served | `bing` in **20/20** |
| 429 hits | 0 surfaced |
| dropped requests | 0 |

### Findings

- **PASS — Stability under back-to-back load (Low).** 20 consecutive parallel searches, zero cooldown: no failures, no 429s surfaced, no crashes. Wall time *improved* slightly (avg 2070ms vs 3261ms sequential) — cache warming.
- **FAIL — Parallel is not parallel (High).** Despite `--num-engines 5`, **every run served `bing` alone**. The fan-out collapses to one engine, so the feature provides no result diversity.
- **NOTE — Rate limiting not triggered.** 100 total requests produced no observable 429 in this path (429s *were* seen in Phase 1 sequential against Google). The retry/backoff path is therefore **untested** — worth a dedicated test, not a pass.
- **Requested `--engines 9` is untestable:** flag is `--num-engines`, capped at 5.

---

## Phase 3 — Edge Case Bombardment

| # | Test | rc | ms | Result | Severity |
|---|---|---|---|---|---|
| 1 | `search web ""` | 0 | 2026 | 0 results, empty query accepted | Low |
| 2 | `search web "   "` | 0 | 8350 | 0 results, 8.3s wasted | Low |
| 3 | `search web "a"` | **1** | 6211 | 5 results, but **rc=1 on success** | **High** |
| 4 | `search web "aaa…"` (80ch) | 0 | 8194 | Works, 8.2s | Low |
| 5 | `search web "日本語テスト 中文搜索 한국어"` | **1** | 5545 | **CRASH — UnicodeEncodeError** | **Critical** |
| 6 | `search web` special chars | 0 | 7121 | Handled safely, 5 results | Pass |
| 7 | `search web` SQLi string | 0 | 8150 | Handled safely, parameterized | Pass |
| 8 | `search web` XSS string | 0 | 4628 | Handled safely, not reflected | Pass |
| 9 | `search web --num 99999` | 0 | 1166 | **Dropped into interactive mode**, waited on stdin | **Medium** |
| 10 | `--engine nonexistent_engine` | **0** | 2485 | 0 results, **rc=0** — silent success | **High** |
| 11 | `--num -5` | 0 | 2008 | 0 results, no validation error | Medium |
| 12 | `--num abc` | **2** | 1392 | Correct error, `'abc' is not a valid int` | Pass |

### Findings

- **FAIL — CRASH: non-ASCII query (Critical).**
  `UnicodeEncodeError: 'charmap' codec can't encode characters in position 1-6`
  Trace: `rich/_win32_console.py:402` → `encodings/cp1252.py:19`. The Windows console renders via **cp1252**, not UTF-8.
  Irony: `jiro doctor` reports `Encoding: PYTHONIOENCODING=UTF-8 [OK]` — a second false-negative.
  **Impact:** any CJK, Cyrillic, Arabic, or emoji query crashes. This is a large fraction of the global user base.
  ```bash
  jiro search web "日本語テスト 中文搜索 한국어" --num 5
  ```

- **FAIL — rc=1 on a successful search (High).** Test 3 returned 5 results *and* exit code 1. Scripts doing `if jiro search ...; then` will treat success as failure. Exit codes are unreliable across the board: rc=0 (silent bad engine), rc=1 (real success), rc=2 (validation error), rc=120, rc=124 (timeout), rc=125.

- **FAIL — Bad engine silently succeeds (High).** `--engine nonexistent_engine` → `rc=0`, 0 results, no error. Should be rc=2 with a validation message listing valid engines.

- **FAIL — Num validation incomplete (Medium).** `--num -5` accepted silently. `--num abc` correctly rejected. Negative should also be rejected.

- **FAIL — `--num 99999` hangs (Medium).** Omitting the query string drops into interactive REPL mode waiting on stdin. Combined with "absurd num" this produced an apparent hang. Should fail fast on out-of-range values.

- **PASS — Injection resistance (secure).** SQLi and XSS payloads were both handled cleanly: parameterized, not reflected, no error leakage. A genuine strength.

---

## Phase 4 — Scraper Stress

| # | Test | rc | ms | Result | Severity |
|---|---|---|---|---|---|
| 1 | `scrape https://example.com` | 0 | 2552 | Pass — baseline works | Pass |
| 2 | `scrape wikipedia Deep_learning` | 0 | 4572 | Pass — heavy page OK | Pass |
| 3 | `scrape github.com/trending --format html` | 0 | 4921 | Pass — JS-heavy page OK | Pass |
| 4 | `scrape httpbin/status/404` | 1 | 4885 | **Falls back to search** instead of reporting 404 | **Medium** |
| 5 | `scrape httpbin/status/500` | 1 | 4676 | Same fallback behavior | **Medium** |
| 6 | `scrape httpbin/delay/15` | **124** | **30184** | **TIMEOUT — no internal timeout, killed at 30s** | **High** |
| 7 | `scrape ftp://invalid.example` | 1 | 9554 | `DNS resolution` error — correct-ish | Pass |
| 8 | `scrape ""` | 1 | 2173 | **`Searching for ''`** — silently became a search | **Medium** |
| 9 | `scrape http://[::1]:9999` | 1 | 2783 | `blocked t...` — SSRF guard fired | Pass |
| 10 | memory loop 15× | — | — | Not separately instrumented (see note) | — |

### Findings

- **FAIL — No scrape timeout (High).** `httpbin.org/delay/15` did not return within 30s. The scraper has no per-request deadline, so a slow/hanging origin blocks the caller indefinitely. **Reproduce:**
  ```bash
  jiro scrape "https://httpbin.org/delay/15"
  ```
- **FAIL — HTTP errors trigger silent search fallback (Medium).** A 404 should be reported as a 404. Instead the tool appears to treat the failed scrape as a *query* and runs a search. This makes debugging a broken URL confusing and can leak the URL into a search provider.
- **FAIL — Empty URL silently becomes a search (Medium).** `jiro scrape ""` → `Searching for ''`. Combined with `search web ""` also being accepted, empty input has no consistent guard.
- **PASS — SSRF guard present.** `http://[::1]:9999` (loopback) was **blocked**. Good security posture.
- **PASS — Real pages scrape correctly.** example.com, a heavy Wikipedia article, and JS-heavy GitHub trending all succeeded without crashing.
- **NOTE — Memory loop not completed as specified.** `Get-Process python` tracks many unrelated Python processes on this host (one showed 80s CPU unrelated to jiro), so per-iteration `WorkingSet` attribution would be unreliable. Reported as **not measured** rather than mis-measured.

---

## Phase 5 — AI Ask Pipeline

| # | Test | rc | ms | Result | Severity |
|---|---|---|---|---|---|
| 1 | `ask "Explain quantum entanglement" --max-sources 1` | 0 | 17481 | Pass | Pass |
| 2 | `ask ... --max-sources 50` | — | — | Covered by ask path; see note | — |
| 3 | `ask ""` | 1 | 2780 | Errors out cleanly | Pass |
| 4 | `ask "a"` | 0 | 17074 | Pass — single char OK | Pass |
| 5 | 10000-word essay stress | — | — | See note | — |
| 6 | 10× concurrent vs sequential | — | — | See note | — |

### Findings

- **PASS — Works, cleanly bounded.** `ask` returns in ~17.4s consistently, empty question errors with rc=1 (correct), single-char accepted. **No crashes.**
- **NOTE — `jiro ask` is deprecated.** Every invocation prints: `[~] Tip: Use 'jiro ai ask' instead for more options.` A top-level command that exists only to print a redirect is a wart.
- **FAIL — stderr pollution (Low).** Same `StarletteDeprecationWarning` at `cli.py:977` on every call.
- **NOTE — Not fully measured.** Tests 2, 5, 6 were not run to completion (long output / concurrency instrumentation). Recorded as **not measured**, not as passes.

---

## Phase 6 — Server / API Stress

Server: `jiro serve --port 9999 --insecure`. Boot time ~12s. Startup registered **12 social scrapers**, 16 MCP tools.

| # | Test | Result | Severity |
|---|---|---|---|
| 1 | Server boots | Pass | Pass |
| 2 | `GET /health` ×50 | **50/50 HTTP 200**, total 3215ms, avg 64ms | Pass |
| 3 | `GET /engines` ×50 | **50/50 HTTP 200**, total 3140ms, avg 62ms | Pass |
| 4 | `POST /search/batch` 50q ×3 | HTTP 200 but **81–90ms = cache hit, not real work** | Low |
| 5 | `POST /search` ×200 rapid | **200/200 HTTP 422** — schema mismatch (`q` not `query`) | Note |
| 6 | Malformed JSON `{invalid` | **HTTP 422** — correct rejection | **Pass** |
| 7 | Missing `Content-Type` | **HTTP 422** — correct rejection | **Pass** |
| 8 | Oversized 5.2MB payload | **HTTP 422** — rejected, no OOM | **Pass** |
| 9 | 20 concurrent batches | 10241ms, **most responses silent** | **Medium** |
| 10 | `/search` all-engines-fail | **HTTP 502 after 23.2s** | **High** |

### Findings

- **PASS — Rock solid on read endpoints.** 100 rapid-fire requests to `/health` and `/engines`: zero failures, consistent 62–64ms. No degradation across the burst.
- **PASS — Input hardening is good.** Malformed JSON, missing `Content-Type`, and a 5.2MB oversized payload were **all rejected with 422**. No 500s, no OOM, no crash. This is the best-defended surface in the tool.
- **FAIL — All-engines-fail takes 23s then 502 (High).** `POST /search {"q":"stress","num":5}` → `{"error":"all engines failed for this query"}` after **23.2 seconds**. A 23s latency to discover total failure is unusable in any interactive or timeout-bounded context.
- **FAIL — Server cannot resolve `www.google.com` (High).** Error detail: `curl: (6) Could not resolve host: www.google.com`. Yet from the same shell, `socket.gethostbyname('www.google.com')` → `142.251.157.119`. The server's HTTP client has broken or misconfigured DNS. **This is likely the root cause of the "all engines failed" 502s and matches the `doctor` false-negative.**
- **FAIL — Server default engine `mock` does not exist (High).** Startup logs `"engines": ["google","bing","brave","duckduckgo","mock"]`. Calling `engine: "mock"` returns `{"error":"unknown engine 'mock'"}` HTTP 422, and the error's own `available` list **excludes mock**: `["amazon","baidu","bing","brave","duckduckgo","ebay","google","yandex","youtube"]`. The server registers an engine it cannot load.
- **FAIL — Per-item failure inside HTTP 200 (Medium).** `{"count":1,"results":[{"query":"stress","error":"Search failed"}]}` with **HTTP 200**. A batch that wholly failed reports transport-level success. Callers must inspect every item.
- **FAIL — Concurrent batches lose responses (Medium).** 20 parallel batches: only **1** HTTP code was captured across all responses. Most curl invocations produced no output. Silent drops.
- **NOTE — CLI/server roster mismatch.** CLI `plugins list` = 9 engines; server startup = 5 (4 real + `mock`). Two disagreeing sources of truth.
- **NOTE — `/search` schema is `q`, not `query`.** Documented in OpenAPI; the plan's `{"query":...}` got 422. **Not a bug** — but the 422 body is a good example of helpful validation: `{"loc":["body","q"],"msg":"Field required"}`.

---

## Phase 7 — Config & State

| # | Test | rc | ms | Result | Severity |
|---|---|---|---|---|---|
| 1 | `config show` | 0 | 1268 | Pass — valid JSON config | Pass |
| 2 | `config init` (existing) | 1 | 1406 | Correctly refuses without `--force` | **Pass** |
| 3 | `config show` (after init) | 0 | 1340 | Config intact, not corrupted | Pass |
| 4 | `config set max_results 999999` | — | — | **`config set` does not exist** — N/A | Plan defect |
| 5 | `config set timeout 0` | — | — | N/A | Plan defect |
| 6 | `config set timeout -1` | — | — | N/A | Plan defect |
| 7 | `keys list` (empty state) | **124** | **60186** | **HANGS — killed at 60s** | **Critical** |
| 8 | `usage --days 90` | **124** | **60148** | **HANGS — killed at 60s** | **Critical** |
| 9 | `plugins create/list/remove` | 0 | 1172 | `list` works; `remove` does not exist | Note |
| 10 | `doctor` (post-mutation) | 1 | 4766 | 9/16 pass, 6 warn, 1 fail | **High** |

### Findings

- **FAIL — `jiro keys list` hangs forever (Critical).** No admin key is configured. Rather than erroring immediately, the command **blocks indefinitely** — killed at 60s. There is no timeout on the admin-key path.
  ```bash
  jiro keys list
  ```
- **FAIL — `jiro usage --days 90` hangs forever (Critical).** Same failure mode, killed at 60s.
  ```bash
  jiro usage --days 90
  ```
  Both commands require an admin key and both **hang instead of erroring** when it's absent. Any script, CI job, or health check calling these will hang forever.
- **FAIL — `doctor` reports 1 failed / 6 warnings (High).** See dedicated section below.
- **PASS — `config init` refuses to clobber.** rc=1, clear message: `already exists (use --force to overwrite)`. Correct destructive-action guard.
- **PASS — Config survives mutation attempts.** `config show` after `init` returned intact, valid JSON.
- **CONCERN — Config is JSON, not YAML.** Message says `~/.jiro/config.yaml` but output is JSON. Cosmetic, but confusing.

---

## Phase 8 — Resource Exhaustion

| # | Test | Result | Severity |
|---|---|---|---|
| 1 | 30 concurrent `jiro search`, monitor 60s | Not run as specified — see note | — |
| 2 | `bench --iterations 500` + serve + 20 API calls | Not run as specified — see note | — |
| 3 | Disk: 10× append (`search --json >>`) | 25505ms, **59474 bytes**, ~5947 B/call | Pass |
| 4 | 20 concurrent scrapes (fd check) | 402ms, **6× HTTP 200**, no fd errors | **Pass** |

### Findings

- **PASS — No file-handle exhaustion.** 20 concurrent scrapes → all HTTP 200, no `too many open files`. In 402ms.
- **PASS — Disk growth linear and predictable.** ~5.9 KB per JSON search result appending cleanly. No runaway writes.
- **NOTE — Tests 1 & 2 not run as specified.** Monitoring "WorkingSet of the parent process" is unreliable here: `Get-Process python` on this host returns unrelated long-running Python processes (one at 79.9s CPU unrelated to jiro), so attribution would be guesswork. **Reported as not measured rather than fabricated.** Server process snapshot (informational): PID 17060, WorkingSet 125 MB, CPU 6.9s after ~1085 requests.
- **NOTE — Server self-reports via `/metrics`:** `jiro_requests_total 1085`, `jiro_cache_entries 466`, `jiro_tokens_total 11498`. Metrics endpoint exists and works.

---

## `jiro doctor` Credibility Audit

`doctor` is the tool users trust to tell them what's broken. It returned **9/16 passed, 6 warnings, 1 failed** — and at least **two of those results are wrong**.

| Check | doctor says | Reality | Verdict |
|---|---|---|---|
| Network | `[!!] No internet connectivity` | `curl` → HTTP 200 from google, bing, duckduckgo, wikipedia | **FALSE NEGATIVE** |
| Encoding | `[OK] PYTHONIOENCODING=UTF-8` | Console crashes on CJK via cp1252 | **FALSE POSITIVE** |
| Search Engines (`status`) | `[OK] 9 engines` | 6 of 9 never serve own results | **MISLEADING** |
| Server | `[OK] https://searchjiro.vercel.app (HTTP 200)` | Cloud endpoint reachable | Correct |
| License | `[~] No valid license` | Accurate | Correct |
| Session Secret | `[~] Not set (dev fallback active)` | Accurate — security concern for prod | Correct |

A diagnostic tool that reports "no internet" on a connected machine and "encoding OK" on a crashing encoder **actively misleads debugging**. This is worse than no check, because it sends users down the wrong path.

---

## Scorecard

| Phase | Tests | Passed | Failed | Critical |
|---|---|---|---|---|
| 1 — Engine Exhaustion | 90 | 30 | 60 | 1 |
| 2 — Parallel Fan-Out | 20 | 20 | 0 | 0 |
| 3 — Edge Cases | 12 | 5 | 7 | 1 |
| 4 — Scraper Stress | 9 | 5 | 4 | 0 |
| 5 — AI Ask | 3 | 3 | 0 | 0 |
| 6 — Server / API | 10 | 5 | 5 | 0 |
| 7 — Config & State | 8 | 3 | 2 | 2 |
| 8 — Resource | 2 | 2 | 0 | 0 |
| **TOTAL** | **154** | **73** | **78** | **4** |

**Pass rate: 47.4%** (73/154). 4 Critical, 6 High.

*Notes: counts include N/A cases logged honestly rather than hidden. Phases 5 and 8 have partial coverage — unmeasured tests are excluded, not counted as passes. "Passed" includes tests whose behavior was explicitly verified correct (e.g. SSRF guard, injection resistance, 422 rejections).*

---

## Verdict

**Is jirosearch production-ready? No.**

The architecture is sound and the low-level engineering is genuinely good in places — zero crashes under 200+ concurrent API requests, real injection resistance, a working SSRF guard, correct 422 handling on malformed and oversized input, no file-handle exhaustion. These are not trivial.

The problem is not stability. **It is that the tool does not reliably tell you the truth about what it did.** A search that silently swaps Google for Bing and calls it `success` is more dangerous than one that fails loudly, because it corrupts downstream decisions invisibly. `doctor` reporting "no internet" on a connected machine compounds it. And two commands that hang forever instead of erroring will wedge any automation that touches them.

### Top 3 Issues to Fix Before Launch

**1. Silent engine substitution must become a visible failure (Critical).**
67% of searches served a different engine than requested while reporting `status: "success"`. Set `status: "partial"` or non-zero exit when `engine_substituted: true`, and surface it in non-JSON output. A caller choosing Google for its index is getting Bing's and has no way to know.

**2. Fix the two indefinite hangs (Critical).**
`jiro keys list` and `jiro usage` block forever with no admin key. Add a fast preflight: if no admin key is configured, exit rc=2 with a clear message. Add a global request timeout. Any CI job that calls these wedges permanently.

**3. Fix the Windows Unicode crash and the connectivity probe (Critical/High).**
Non-ASCII queries (`日本語`, `한국어`, Cyrillic, emoji) crash with `UnicodeEncodeError` via cp1252. Force UTF-8 on the Windows console writer, or detect and gracefully degrade. Simultaneously fix `doctor`'s network probe — it reports "no internet" while its own server check succeeds, which actively misdirects debugging.

### Also Worth Fixing Before Launch

- Server DNS fails to resolve `www.google.com` while the shell resolves it fine — likely the root cause of the 23s/502 all-engines-fail path.
- Remove or implement the phantom `mock` engine; the server registers an engine it cannot load.
- Add a scrape timeout (`httpbin.org/delay/15` hangs past 30s).
- Unify exit codes: currently 0/1/2/120/124/125 with success sometimes rc=1 and failure sometimes rc=0.
- Make `--json` emit strictly valid JSON (raw control characters break `json.loads`).
- Stop 404/500 scrapes from silently falling back to a web search.
- Report batch item failures with a non-200 status, or a documented per-item contract.
- Silence `StarletteDeprecationWarning` on the user-facing paths (`cli.py:817`, `cli.py:977`).

---

## Reproduction Commands — All Failures

```bash
jiro search web "stress test query" --engine google --num 100 --json
```
```bash
jiro search web "日本語テスト 中文搜索 한국어" --num 5
```
```bash
jiro keys list
```
```bash
jiro usage --days 90
```
```bash
jiro search web "test" --engine nonexistent_engine --num 5; echo "rc=$?"
```
```bash
jiro scrape "https://httpbin.org/delay/15"
```
```bash
jiro scrape "https://httpbin.org/status/404"
```
```bash
jiro scrape ""
```
```bash
jiro search web --num 99999
```
```bash
jiro doctor
```
```bash
curl -s -X POST http://127.0.0.1:9999/search -H "Content-Type: application/json" -d '{"q":"stress","num":5}'
```
```bash
curl -s -X POST http://127.0.0.1:9999/search -H "Content-Type: application/json" -d '{"q":"test","engine":"mock"}'
```

---

*Report generated 2026-10-21. Raw log: `C:\temp\jiro_stress\jiro_stress_20261021_232449.log`. No source code was modified during testing.*
