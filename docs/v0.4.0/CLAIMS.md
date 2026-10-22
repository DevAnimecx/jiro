# JIRO v0.4.0 — Claims Protocol

**Purpose:** every public performance number must be reproducible from a committed artifact. If the artifact does not exist, the number is not published. If the artifact misses the target, the number changes.

This document defines the only approved forms of the 0.4.0 claims: **2x faster**, **3x more efficient**, **15+ agents, one command**, and **citation-complete**.

---

## 1. Claim registry

`benchmarks/claims.yaml` is the machine-readable registry.

```yaml
version: 1
claims:
  - id: speed-2x
    public: "2x faster on multi-source research"
    metric: wall_clock_p50_seconds
    baseline: "0.3.3"
    workload: research_5q_10src
    target_ratio: 2.0
    artifact: docs/v0.4.0/benchmarks/speed.json
    owner: engine

  - id: efficiency-3x
    public: "3x fewer tokens per verified citation"
    metric: tokens_per_verified_citation
    baseline: raw_markdown
    workload: research_5q_10src
    target_ratio: 3.0
    quality_gate: non_inferior
    artifact: docs/v0.4.0/benchmarks/efficiency.json
    owner: evidence

  - id: setup-15
    public: "15+ agents, one command"
    metric: tier_a_clients_passing
    target: 15
    artifact: docs/v0.4.0/benchmarks/setup.json
    owner: fabric

  - id: citation-complete
    public: "every claim cites a source"
    metric: non_gap_claims_with_source_ratio
    target: 1.0
    artifact: docs/v0.4.0/benchmarks/citations.json
    owner: evidence
```

A release test loads the registry, loads each artifact, recomputes the ratio, and fails the release job if any claim is unmet.

---

## 2. Claim 1 — 2x faster

### Definition

> On workload W1, the median wall-clock time to produce a complete cited result is at least half of JIRO 0.3.3 on the same machine, network, and source set.

`ratio = T_baseline_p50 / T_0.4.0_p50 >= 2.0`

### Workload W1: `research_5q_10src`

- One natural-language research goal.
- Five sub-queries covering distinct evidence needs.
- Ten sources fetched and extracted.
- One cited result with at least eight verified citations.
- Cache state recorded separately: `cold`, `warm-query`, `warm-node`.
- 30 runs per state.

### Baseline

JIRO 0.3.3, same machine, same network:
- sequential `/v1/search` calls for the sub-queries;
- sequential `/v1/scrape` calls for the sources;
- the existing `/v1/ai/agent` path if it can complete the workload;
- otherwise the documented sequential composition, committed as a script.

The baseline script is part of the artifact. A baseline nobody can run is not a baseline.

### What counts

- Wall clock from request start to complete payload, measured client-side.
- Network retries included.
- Engine rate-limit waits included.
- Warm-up excluded; reported separately.
- Failures included in the denominator. A fast failure is not a speed win.

### What does not count

- Excluding slow engines after the fact.
- Comparing against a degraded baseline.
- Measuring only the scheduler.
- Combining cold and warm runs.
- Using a different source set for baseline and 0.4.0.

### If it misses

- Ratio >= 2.0: publish.
- 1.5 <= ratio < 2.0: publish the measured ratio, not "2x".
- Ratio < 1.5: remove the speed headline. Keep the graph if efficiency and setup pass.

---

## 3. Claim 2 — 3x more efficient

### Definition

> For the same sources and non-inferior answer quality, the tokens required per verified citation are at least three times lower than raw markdown.

`ratio = tokens_per_verified_citation_baseline / tokens_per_verified_citation_0.4.0 >= 3.0`

### Workload W2

- Same ten sources as W1.
- Same required citation targets.
- Baseline input: raw markdown of those sources, trimmed only by the existing scraper output.
- 0.4.0 input: evidence packet with the same claims available.
- Tokenizer named and fixed (`o200k_base` when `tiktoken` is installed; estimator otherwise with published error).

### Quality gate

Non-inferiority on the 1-10 rubric:
- LLM judge, fixed model and prompt, 30 runs.
- 50 human spot checks, blind.
- Lower bound of the 95% confidence interval no worse than -2 points versus baseline.

If quality fails, the efficiency claim is void regardless of the ratio.

### Anti-gaming rules

- A "citation" must resolve to a source span that supports the claim. Counting links is not counting citations.
- Removing claims to reduce tokens is not efficiency; the required claim set is fixed before measurement.
- Truncating mid-quote is a hard failure.
- The packet must stay under its declared budget.

### If it misses

- Ratio >= 3.0 and quality passes: publish.
- 2.2 <= ratio < 3.0: publish the measured ratio, not "3x".
- Ratio < 2.2: remove the efficiency headline.

### Publishing while the gate is pending

The gate can lag the ratio by a long way — 30 judge runs and 50 blind human spot
checks are not something one session produces. Rather than hold a measured
number out of the release, or quietly drop the gate, a claim may be published
**with a caveat**, under all of the following:

- `benchmarks/claims.yaml` carries a `caveat:` string on the claim. The release
  check treats the gate as satisfied only when a caveat is present, and prints
  it in the report.
- The caveat travels verbatim wherever the number appears: release notes,
  README, docs, website.
- The number is stated as what it is (tokens per citation), never as a claim of
  equivalent answer quality.
- The caveat comes out the moment the gate passes: a declared caveat on a
  passed gate fails the release check, so it cannot rot in place.
- If the gate later fails, the ratio is withdrawn in the next release.

`benchmarks/report.py` enforces the first and fourth rules.

---

## 4. Claim 3 — 15+ agents, one command

### Definition

> A clean machine can install JIRO, configure at least 15 supported agent clients, and complete a first successful tool call with one command, in a median of two minutes or less.

### Matrix

See `AGENT_MATRIX.md`. A client counts only if:
- it is detected automatically;
- its config is written without corrupting existing entries;
- the MCP handshake succeeds;
- one real `search_graph` call returns a valid result;
- `jiro setup --uninstall` restores the original config byte-for-byte.

### Test environment

- Clean Windows 11, macOS 14+, Ubuntu 24.04 VMs.
- No pre-existing JIRO config.
- Network access to the public engines used by the smoke test.
- Python 3.11+ and `uv` available; `pipx` fallback tested.

### Metrics

- `tier_a_clients_passing` >= 15.
- `median_time_to_first_call_seconds` <= 120.
- `corrupted_configs` = 0.
- `rollback_failures` = 0.

### If it misses

- 15 pass: publish.
- 12-14 pass: publish "12+ agents", list the gaps.
- Below 12: do not use the agent-count headline.

---

## 5. Claim 4 — citation completeness

### Definition

> Every non-gap claim in a packet resolves to at least one source, and every quote matches its source span verbatim.

`ratio = non_gap_claims_with_valid_source / non_gap_claims = 1.0`

### Checks

- Source id exists in the packet.
- URL is well-formed and was fetched in this session or a recorded cache entry.
- Quote span matches the normalized source text exactly.
- Numeric claims carry a unit and a source.
- `unsourced` is an explicit claim kind, never a silent default.

### If it misses

Any ratio below 1.0 blocks release. This is a correctness gate, not a marketing claim.

---

## 6. Artifact schema

Every artifact is JSON with this envelope:

```json
{
  "claim_id": "speed-2x",
  "created_at": "2026-11-01T12:00:00Z",
  "commit": "abc1234",
  "version": "0.4.0",
  "hardware": {"cpu": "...", "ram_gb": 16, "os": "Windows 11", "python": "3.12.4"},
  "network": {"class": "residential", "region": "IN"},
  "workload": "research_5q_10src",
  "cache_state": "cold",
  "runs": 30,
  "metrics": {
    "baseline": {"p50": 8.4, "p95": 11.2, "unit": "s"},
    "candidate": {"p50": 3.9, "p95": 5.1, "unit": "s"},
    "ratio": 2.15
  },
  "quality": {"rubric": "1-10", "baseline": 7.8, "candidate": 7.9, "ci_low": -0.1},
  "reproduction": "python -m benchmarks.run --workload research_5q_10src --runs 30",
  "notes": "Engine X rate-limited in 2 runs; included."
}
```

Rules:
- Ratios are computed from raw runs, not from rounded summaries.
- Failures are included and reported.
- The artifact is committed, not attached to a chat.
- A rerun on different hardware produces a new artifact; it does not overwrite the old one.

---

## 7. Release enforcement

The release job:

1. Loads `benchmarks/claims.yaml`.
2. Verifies every artifact exists and matches the claim id and version.
3. Recomputes ratios from `runs` if raw data is present; otherwise verifies the recorded computation hash.
4. Fails if any claim is unmet or any public string contains a prohibited term.
5. Writes a `claims-report.json` attached to the release.

The launch checklist cannot be marked complete without a green claims job.

---

## 8. Prohibited language

Do not publish:

- "AGI", "AGI-level", "human-level".
- "2x faster than Google", "3x faster than Perplexity", or any competitor comparison without a same-workload, same-machine artifact.
- "Zero latency", "infinite context", "unlimited sources".
- "LoRA", "weight injection", "attention editing", "model parameter update".
- A ratio without naming the workload, baseline, and cache state.
- A benchmark result from a different hardware profile presented as universal.

The only approved forms are the ones defined above, with the artifact linked.
