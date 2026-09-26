# Changelog

All notable changes to Jiro will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.1] - 2026-09-26

### Added
- Official package alignment across the CLI, JavaScript SDK, Python SDK, and Cloud API at `0.3.1`.
- **Auto-heal update system** (`heal_pip_install()`, `_kill_jiro_exe()`, `_is_winerror_32()`) in `self_healing.py` for automatic WinError 32 recovery during pip upgrades.
- Built-in engine plugin auto-registration for arXiv, GitHub, Google Scholar, Hacker News, Reddit, and Wikipedia.
- Cloud pricing endpoint and synchronized credit actions for search, scrape, AI, agent, MCP, and AEO feed operations.
- CI workflow: pytest (non-network tests) + pip-audit + gitleaks on every push.
- Release guard tests for social normalizers and the curl response adapter (15 tests).

### Changed
- Hardened internal authentication, API-key ownership checks, WAF query normalization, and circuit-breaker failure handling.
- Improved async session validation and Firebase realtime token refresh across web authentication flows.
- Updated CLI auth, dashboard navigation, favicon branding, and public documentation for the completed release.
- Test suite runs on pytest 9.1.1 / pytest-asyncio 1.4.0 (835 tests).
- **Windows update path**: `.bat` updater now force-kills `jiro.exe` via `taskkill /F /IM jiro.exe` before pip install, with retry loop and exponential backoff (up to 5 attempts).

### Fixed
- **WinError 32 on pip upgrade**: `jiro.exe` no longer blocks `pip install --upgrade jirosearch`. The updater kills the running process and retries automatically with exponential backoff.
- Device authorization now resolves the Firestore user document before issuing API keys.
- Local Firestore emulator flows no longer report an unconfigured server.
- Cloud credit revocation, malformed request handling, and metrics shutdown paths are safe and consistent.
- `normalize_profile` no longer raises `AttributeError` on `SocialProfile` objects (type defaults to `profile`); dict results from scrapers pass through unchanged.
- `_CurlResponseAdapter` provides `raise_for_status()` and `json()` for httpx-compatible scraping paths.

## [0.3.0] - 2026-09-15

### Added
- **RFC 8628 Device Code Flow**: CLI authenticates via browser. Works everywhere — SSH, Docker, headless servers.
- **Encrypted Credential Storage**: AES-256-GCM with machine-derived PBKDF2 key. No plaintext on disk.
- **Self-Hosted 3-Tier Pricing**:
  - **Free**: 100 RPM, 10K RPD
  - **Pro**: 500 RPM, 100K RPD, AI search
  - **Enterprise**: 1,000 RPM, 1M RPD, white-label
- **HMAC-SHA256 License Validation**: Offline-first, hardware-bound, no server needed after activation.
- **CLI License Commands**: `jiro license activate`, `info`, `deactivate`, `validate`
- **Device Code Polling Rate-Limited** per RFC 8628.
- License keys hardware-bound, max 3 devices, with 24-hour grace period for renewal.

### Bug Fixes
- Fixed `jose importJwk` breaking middleware auth.
- Fixed admin pages snake_case vs camelCase.
- Fixed device auth API key creation in Firestore.
- Fixed pricing data YAML inconsistencies.

### Security
- Device code polling rate-limited per RFC 8628.
- License keys hardware-bound, max 3 devices.
- 24-hour grace period for license renewal.

## [0.2.15] - 2026-09-10

### 🚀 THE COMPLETE RELEASE - Everything from v0.2.13, v0.2.14, and v0.2.15 combined

### Added

#### Stealth & Anti-Bot Engine (v0.2.13)
- **TLS/JA3 Fingerprint Rotation**: Browser impersonation with 20+ profiles (Chrome, Firefox, Safari, Edge, Tor)
- **Behavioral Simulation**: Randomized timing, mouse movements, scroll patterns
- **curl-cffi Integration**: Native TLS fingerprint spoofing

#### Real-time Streaming (v0.2.13)
- **WebSocket Endpoints**: `/ws/search`, `/ws/search/stream`, `/ws/monitor`
- **Connection Manager**: Track and manage active WebSocket connections
- **SSE Streaming**: Server-Sent Events for real-time search results

#### Search Enhancements (v0.2.13)
- **Parallel Search**: Multi-engine search with `--parallel` flag
- **Configurable Engines**: `--engines` flag to control concurrent engine count (max 5)
- **Interactive Mode**: `jiro search -i` for continuous search loop

#### CLI Improvements (v0.2.13)
- **Benchmark Command**: `jiro bench` for performance testing with statistics
- **Search Query Auto-Scrape**: `jiro scrape "query"` searches then scrapes top result
- **Auto-HTTPS**: Auto-prepend `https://` for bare domains
- **Windows Unicode Fix**: cp1252 compatible output on Windows

#### Enterprise Features (v0.2.14)
- **Rate Limiting**: Sliding window rate limiter with per-second, per-minute, per-hour, per-day limits
- **Usage Quotas**: Monthly quotas for searches, scrapes, and AI queries
- **Tier-based Limits**: Free, Pro, and Enterprise tiers with different limits
- **Rate Limit Headers**: X-RateLimit-* headers in API responses

#### Plugin System (v0.2.14)
- **Plugin Base Classes**: SearchEnginePlugin, ScraperPlugin, AIProviderPlugin
- **Plugin Registry**: Register, unregister, and discover plugins
- **Plugin Discovery**: Auto-discover plugins from a directory
- **Plugin Hooks**: Event-based hook system for plugin communication

#### Advanced Caching (v0.2.14)
- **Memory Cache**: LRU/LFU in-memory caching with analytics
- **Cache Analytics**: Hit rate, miss rate, eviction count, memory usage
- **Cache Strategies**: LRU, LFU, TTL, Write-through
- **Cache Keys**: SHA-256 based key generation

#### Multi-language SDKs (v0.2.14)
- **Python SDK**: Synchronous and async clients with full API coverage
- **JavaScript/TypeScript SDK**: Browser and Node.js compatible client
- **Go SDK**: Idiomatic Go client with functional options

#### Monitoring & Observability (v0.2.15)
- **Metrics Registry**: Prometheus-compatible metrics collection
- **Request Tracing**: Correlation IDs with span tracking
- **Health Checks**: Liveness and readiness probes
- **Performance Metrics**: Counters, gauges, histograms with statistics

#### Export/Import (v0.2.15)
- **Search Export**: Export results to JSON, CSV, Markdown, YAML
- **Config Export**: Backup and restore configurations
- **Backup Manager**: Create and restore system backups

#### Scheduled Searches (v0.2.15)
- **Cron Scheduling**: Cron-like schedule expressions
- **Recurring Jobs**: Create, pause, resume, delete scheduled searches
- **Notifications**: Email and webhook notifications on results

#### Search History (v0.2.15)
- **History Tracking**: Record all searches with timestamps
- **History Query**: Search by text, engine, time range, user
- **Analytics**: Popular queries, time distribution, cache hit rate

#### Batch Operations (v0.2.15)
- **Batch Search**: Execute multiple searches concurrently
- **Batch Scrape**: Scrape multiple URLs in parallel
- **Mixed Operations**: Combine search and scrape in one batch
- **Progress Tracking**: Real-time progress and result aggregation

### Changed
- Version bumped to 0.2.15 - The Complete Release
- Improved DNS error messages with hostname and cause
- CLI commands bypass auth for easier local usage
- Custom favicon from logo

### Fixed
- Schema field shadowing in `StructuredExtractRequest`
- curl-cffi impersonation profiles updated to supported versions
- Windows Unicode encoding crash in CLI output (cp1252 compatibility)

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