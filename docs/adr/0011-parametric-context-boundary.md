# ADR 0011 — The parametric output boundary

- **Status:** Proposed
- **Date:** 2026-10-21
- **Deciders:** JIRO core
- **Supersedes:** the DIaaP sections of the v0.4.0 PDF plan
- **Related:** `docs/v0.4.0/PLAN.md`, `docs/v0.4.0/MASTER_PLAN.md`, ADR 0002 (task ownership), ADR 0010 (retrieval and provenance)

## Context

The v0.4.0 source plan proposed "Dynamic Internet-as-a-Parameter" (DIaaP): convert scraped
web content into a low-rank attention adapter and inject it into
`model.layers.gated_cross_attention` during inference, so the model attends to live web
state without spending prompt tokens.

JIRO is a local-first search and scraping service. It does not own a forward pass:

- `pyproject.toml` has no `torch`, `transformers`, `onnx`, or GPU dependency.
- The supported model providers (`jiro/ai/llm.py`) are OpenAI-compatible HTTP endpoints,
  Anthropic, and Gemini. None exposes attention hooks or accepts weight deltas.
- Ollama, the only local runtime in the supported set, exposes generation and embeddings,
  not per-layer attention injection.
- Even with a co-resident `transformers` process, attention injection is model-specific,
  version-specific, and incompatible with quantized or paged-attention kernels.

Shipping "DIaaP" as described would require JIRO to become an inference runtime. That is a
different product, a different dependency profile, and a different security surface.

At the same time, the problem the plan names is real: raw markdown dumps waste context,
inflate cost, and bury the model in boilerplate. That problem is solvable at the boundary
JIRO actually owns: the conversion from web content to model input.

## Decision

1. **JIRO does not inject weights or attention values into any model in v0.4.0.**
   No copy, no documentation, no API field may imply otherwise.

2. **The v0.4.0 deliverable is the evidence packet.** A versioned, validated structure
   containing bounded segments, each with source ids, timestamps, taint flags, and a
   provenance digest, plus a measured token count.

3. **Compression happens before tokenization, not after.** Boilerplate removal, claim and
   quote extraction, near-duplicate collapse, and budgeted selection are the mechanism.

4. **An embedding export is permitted as an explicitly experimental feature.** It emits
   retrieval vectors (float16, named model and dimension) for runtimes that accept prompt
   embeddings. It is documented in `ADAPTER_SPEC.md` as vectors, never as attention edits.

5. **If a future release ships a real adapter, it requires a new ADR** that names the
   supported runtime, the model family, the kernel constraints, and the safety tests.
   It does not ride in on the v0.4.0 name.

## Consequences

### Positive

- The claim matches the code. Every number in the launch can be reproduced from a packet
  artifact.
- No GPU dependency, no model licensing, no kernel compatibility matrix.
- The packet is useful to every client, including remote APIs that will never accept
  embeddings.
- Safety work moves to the real boundary: untrusted text entering a model context.

### Negative

- The headline mechanism is less exotic. "Evidence packets" is a harder sell than
  "inference-time LoRA".
- Compression quality becomes the differentiator, so it must be measured, not asserted.
- The optional embedding path needs a reference integration or it will rot. It is
  therefore gated and may ship marked experimental.

### Neutral

- `jiro/parametric/` is a new subpackage. It does not touch existing response shapes.
- The feature is off by default. Operators opt in.

## Alternatives considered

**A. Co-resident inference runtime with real attention injection.**
Rejected for v0.4.0. Requires GPU, model weights, per-model hooks, and a security review of
arbitrary tensor injection. It also breaks local-first for anyone without a GPU.

**B. Soft prompts only.**
Rejected as the primary mechanism. Prompt-embedding support is uneven across runtimes and
unusable with hosted APIs. Kept as the experimental export in decision 4.

**C. Do nothing; keep raw markdown.**
Rejected. Token bloat is the stated customer problem and the graph layer makes it worse by
increasing source count.

## Enforcement

- Docs lint (warning, not blocking): flag `LoRA`, `attention injection`, `weight update`, and `gated_cross_attention` outside `docs/v0.5/` research notes.
- Packet schema test: `used_tokens <= budget_tokens` on generated cases.
- Launch checklist: every numeric claim links to a benchmark artifact.
- Review rule: a PR that adds a model dependency to core requires a superseding ADR.
