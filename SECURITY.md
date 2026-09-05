# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.2.x   | ✅ |
| 0.1.x   | ✅ |
| < 0.1   | ❌ |

## Reporting a Vulnerability

We take security seriously. If you discover a security vulnerability in Jiro,
please **do not open a public GitHub issue**.

Instead, report it privately:

- **GitHub Security Advisories**: use the "Security" tab → "Report a vulnerability"
- **Email**: security@blackvault.dev (PGP optional)

We will acknowledge within **48 hours**, aim for a fix within **7 days** for
critical issues, and coordinate a public disclosure after a patch is released.

## Scope

This policy covers the Jiro Search API (`jirosearch` PyPI package). For vulnerabilities in the landing page (jiro.dev), see the [landing page repository](https://github.com/DevAnimecx/jirosearch/security).

## Security Features

- **HMAC License Tokens** — Enterprise tier authentication via `Authorization: License <token>` header
- **Rate Limiting** — Per-tier RPM/RPD enforcement
- **SSRF Protection** — Internal network access blocked
- **RBAC** — Role-based access control for multi-tenant deployments
- **Audit Logging** — Full request/response audit chain (Enterprise)
- **Encryption** — Field-level encryption for sensitive data (Enterprise)

## Responsible Use Note

Jiro is a web-scraping and search tool. Users are responsible for complying with
each search engine's Terms of Service, `robots.txt`, and applicable law
(including GDPR/CCPA data-handling requirements). Jiro ships a compliance layer
(robots.txt parsing, ToS tracking, audit logging) to help — but compliance is
the operator's responsibility.

## Supply Chain

- Dependencies are pinned in `pyproject.toml`.
- Docker images are built from `python:3.12-slim` with reproducible layers.
- SBOM and image signing are on the roadmap for the Enterprise edition.
