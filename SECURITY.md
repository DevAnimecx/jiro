# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
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
