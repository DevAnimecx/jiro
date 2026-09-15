## What's New

### RFC 8628 Device Code Flow
CLI authenticates via browser. Works everywhere — SSH, Docker, headless.

### Encrypted Credential Storage
AES-256-GCM with machine-derived PBKDF2 key. No plaintext on disk.

### Self-Hosted 3-Tier Pricing
- **Free**: ₹0 — 100 RPM, 10K RPD
- **Pro**: ₹4,999 one-time — 500 RPM, 100K RPD, AI search
- **Enterprise**: ₹14,999 one-time — 1,000 RPM, 1M RPD, white-label

### HMAC-SHA256 License Validation
Offline-first, hardware-bound, no server needed after activation.

### CLI License Commands
```
jiro license activate <KEY>
jiro license info
jiro license deactivate
jiro license validate <KEY>
```

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
pip install jirosearch==0.3.0
```
