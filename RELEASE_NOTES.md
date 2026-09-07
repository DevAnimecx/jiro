## 🚀 v0.2.12 — Universal Integration, Deep Enterprise Lock, Fast Install

### 🆕 New Features
- **MCP Setup Wizard** — One-command setup for Claude Desktop, Cursor, OpenCode, Codex CLI, OpenClaw, Hermes, Continue.dev, and Zed
- **AES-256-GCM License Encryption** — Enterprise license tokens now encrypted at rest
- **CapabilityToken HMAC Tokens** — Cryptographic license validation with scoped capabilities
- **jiro doctor** — Built-in diagnostics command for quick health checks

### 🔒 Enterprise Deep Lock
- `require_tier("enterprise")` enforced on all 18 `/enterprise/*` endpoints
- `Authorization: License <token>` header validation via HMAC-SHA256
- Enterprise-only features: `ai_search`, `advanced_healing`, `high_volume`, `custom_models`, `commercial_use`, `premium_support`, `white_label`

### 🆓 Free Tier Power-Up
- 100 RPM / 10,000 RPD / 50 max results / 20 concurrent / 25 batch
- Unlocked: `basic_search`, `basic_scrape`, `social_advanced`, `social_search`, `social_timeline`, `smart_search`, `structured_extraction`, `social_batch` (5/batch), `self_learning` (basic)
- Anonymous users get 12 free features (was 3)

### 🐛 Bug Fixes
- Fixed Tuple import error in `feature_flags.py`
- Fixed SQLite parameter binding in `pro.py`
- Fixed payload scoping in `licensing.py`
- Fixed MCP error messages for missing dependencies
- Fixed `SERVER_VERSION` constant in MCP module

### 📦 Install
```bash
pip install jirosearch==0.2.12
```

**PyPI**: https://pypi.org/project/jirosearch/0.2.12/
**Docs**: https://jiro.dev
