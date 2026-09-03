# Changelog

All notable changes to Jiro will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1] - 2026-09-03

### Security

- **CRITICAL**: Auth now enabled by default (was disabled, exposing all endpoints)
- **CRITICAL**: Removed hardcoded PostgreSQL credentials from default config
- **CRITICAL**: CORS origins now empty by default (was `["*"]`)
- **HIGH**: Removed API key support from query strings (header-only auth now)
- **HIGH**: Sanitized all error messages to prevent information leakage
- **HIGH**: JWT secret validation improved with better error messages
- **HIGH**: Nitter URL now defaults to HTTPS
- **MEDIUM**: Added proper database connection cleanup in ProManager
- **MEDIUM**: Improved logging for security-critical operations

### Changed

- Authentication is now enabled by default for all new installations
- API keys must be sent via `X-API-Key` header or `Authorization: Bearer` header
- Query parameter authentication (`?api_key=...`) is disabled by default
- Error responses now return generic messages; full details logged server-side only

### Fixed

- Indentation error in MCP server tool implementations
- Database connection leak in ProManager singleton

## [0.2.0] - 2026-09-03

### Added

#### Phase 1: Search Intelligence
- Hybrid search combining keyword, semantic, and freshness signals
- Cross-encoder reranking with configurable models
- Semantic embeddings for vector similarity search
- Relevance scoring with multi-signal ranking
- Search filters (domain include/exclude, time range, category)
- Highlight extraction for query-aware snippets
- Answer synthesis from search results
- Multi-query expansion for complex topics

#### Phase 2: Social Scraping (12 Platforms)
- Reddit (posts, comments, subreddits)
- Hacker News (stories, comments)
- YouTube (videos, channels, playlists)
- Bluesky (posts, profiles)
- Twitter/X (tweets, profiles, threads)
- Threads (posts, profiles)
- Instagram (posts, stories, profiles)
- TikTok (videos, profiles)
- LinkedIn (posts, profiles, companies)
- Facebook (posts, profiles, groups)
- Telegram (messages, channels, groups)
- Pinterest (pins, boards)

#### Phase 3: Advanced Features
- Structured data extraction with JSON schema
- Intent classification (16 intent types)
- Smart search with auto-routing
- Enhanced plugin system (5 types: engine, search, datasource, extractor, social)
- 6 new engine plugins (Scholar, arXiv, GitHub, Wikipedia, HN, Reddit)
- 6 search plugins (reranker, deduplicator, domain filter, freshness boost, source authority, snippet enricher)
- 3 datasource plugins (SEC filings, clinical trials, patents)

#### Phase 4: Pro Tier
- 4-tier plan system (Free, Starter, Pro, Enterprise)
- API key authentication with tiered access
- Rate limiting (token bucket per API key)
- Quota management (daily request limits)
- Usage tracking and analytics
- Pro router with management endpoints

#### Phase 5: Production Ready
- Docker support (Dockerfile + docker-compose.yml)
- Kubernetes Helm chart
- OpenAPI 3.1 specification
- SDK generation scripts (Python, TypeScript, Go)
- Web UI dashboard (Alpine.js + Tailwind)
- Comprehensive documentation
- Security audit and hardening

### Changed

- Version bumped from 0.1.2 to 0.2.0
- Enhanced MCP server with 12 tools (was 3)
- Improved error handling across all endpoints

### Fixed

- Various edge cases in search engine parsers
- Rate limiter memory leaks on long-running instances
- Cache invalidation race conditions

## [0.1.2] - 2026-08-15

### Added
- Initial release with 9 search engines
- Basic scraping with markdown extraction
- MCP server support
- SQLite caching
- API key authentication

### Fixed
- Various parser bugs
- Memory leaks in HTTP client