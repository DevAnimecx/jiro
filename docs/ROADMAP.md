# Jiro Roadmap — Open Core & Monetization

Jiro is **open-source (MIT)** and will always be free to self-host. The
sustainable model is **open core**: the code is free; convenience, compliance
and support are offered as services.

## Editions

| Edition | License | What you get | For |
|---------|---------|--------------|-----|
| **Jiro OSS** | MIT | Full search/scrape/AI/MCP, all 9 engines, plugins, compliance, Helm/Docker | Everyone — $0 |
| **Jiro Cloud** *(roadmap)* | SaaS | Managed hosting, auto-scaling, global residential proxy pool, SLA, SSO, compliance dashboard, MCP gateway | Teams & AI agents |
| **Jiro Enterprise** *(roadmap)* | BSL-1.0 | Air-gapped binary, SOC 2 path, dedicated support, private engine plugins, custom extractors | Fintech / Legal / Gov / AI labs |

## Why this doesn't challenge open source

1. **Community edition is complete** — no crippled features, no bait-and-switch.
2. **Cloud sells convenience, not code** — same binary, we run it for you.
3. **Enterprise sells compliance & support** — air-gapped, legal review,
   indemnification.
4. **Marketplace creates ecosystem** — community builds, we curate + take 20%.

## Planned milestones

- [ ] **v0.1.x** — stabilize OSS, grow engine plugin marketplace (community).
- [ ] **v0.2** — PostgreSQL backend, distributed rate limiter, web UI dashboard.
- [ ] **v0.4.0** — three-phase engine track: parallel query graph, evidence packets,
      production surface. See [MASTER_PLAN.md](v0.4.0/MASTER_PLAN.md),
      [CLAIMS.md](v0.4.0/CLAIMS.md), and
      [ADR 0011](adr/0011-parametric-context-boundary.md). Track A (durable runtime,
      memory, research repository, orchestration, verification) is recorded in the
      phase handoffs; the release ships when both tracks pass their gates.
- [ ] **Jiro Cloud (beta)** — managed proxy fleet, Free/Developer/Team/Scale tiers.
- [ ] **Jiro Enterprise** — BSL-1.0 air-gapped license, SOC 2 Type II, Helm hardened.
- [ ] **Plugin marketplace** — curated premium plugins (SEC, patents, clinical
      trials, real estate) with revenue share.

## How to support Jiro

- ⭐ Star the repo: https://github.com/DevAnimecx/jiro
- 🐛 File issues & PRs
- 🔌 Build an engine plugin
- ☕ Sponsor: https://github.com/sponsors/DevAnimecx
