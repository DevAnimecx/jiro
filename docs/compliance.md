# Compliance & Responsible Use

Jiro is a **web scraping and search tool**. Because it issues real HTTP requests
to third-party sites, operators must comply with those sites' Terms of Service,
`robots.txt`, and applicable law (including GDPR/CCPA). Jiro ships a **compliance
layer** to help — but compliance remains the operator's responsibility.

## robots.txt

When `scraping.robots_txt.enabled` is `true` (default), Jiro checks
`robots.txt` before each search. In non-strict mode it logs a warning and
continues; in `strict_mode: true` it refuses disallowed paths. Crawl-delay is
respected between requests.

```yaml
scraping:
  robots_txt:
    enabled: true
    strict_mode: false
    user_agent: "JiroBot/1.0 (+https://github.com/DevAnimecx/jiro)"
    cache_ttl_seconds: 3600
```

## Terms-of-Service acknowledgments

`ComplianceManager` tracks ToS acceptance per engine and user. Records are
persisted to the `tos_acknowledgments` SQLite table and expose:

- `POST /compliance/acknowledge` — record acceptance (IP, UA, version)
- `GET /compliance/acknowledged?engine=...` — check acceptance (in-memory + DB)
- `GET /compliance/report` — full compliance report (admin)

## Audit logging

`AuditMiddleware` logs method, path, status and latency. The `ComplianceLogger`
records search events (engine, query, cached, results count) without storing
query parameters, request bodies or API keys — no PII/secret leakage.

## GDPR / CCPA notes

- **Data minimization**: queries are not logged by default (`privacy.log_queries:
  false`).
- **Right to erasure**: the SQLite DB can be cleared with `jiro db` tooling or by
  deleting `~/.jiro/jiro.db`.
- **Data residency**: because Jiro is self-hosted, all data stays in your
  infrastructure.

## Best practices

1. Set a descriptive `user_agent` identifying your project.
2. Enable `strict_mode` for conservative crawling.
3. Use BYOK residential proxies and respect rate limits.
4. Keep `tos_acknowledgments` for audit trails.
