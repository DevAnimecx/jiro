## What's New in 0.3.1

### Realtime CLI Authentication
Browser-based device authorization with Firebase ID-token refresh, resilient loading states, and fresh CLI keys on every authorization.

### Unified 0.3.1 Packages
CLI, JavaScript SDK, Python SDK, and Cloud API are aligned on `0.3.1`.

### Hardened API Surface
- Synchronized credit pricing across search, scrape, AI, agent, MCP, and AEO actions
- Stronger WAF query normalization and internal authentication
- Correct Firestore user resolution for device-authorized keys
- Explicit circuit-breaker engine-unavailable responses

### Local Development
Firestore emulator support works end-to-end for device code, authorization, token polling, and CLI login.

## Bug Fixes
- Fixed jose importJwk breaking middleware auth
- Fixed admin pages snake_case vs camelCase
- Fixed device auth API key creation in Firestore
- Fixed pricing data YAML inconsistencies

## Security
- Device code polling rate-limited per RFC 8628
- License keys hardware-bound, max 3 devices
- 24-hour grace period for renewal

## Install
```bash
pip install jirosearch==0.3.1
```
