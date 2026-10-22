# JIRO v0.4.0 — Agent Matrix

**Purpose:** define exactly which agent clients JIRO supports, how each one is configured, and what "supported" means. A client is not supported because a config snippet exists in a blog post. It is supported when a clean-VM test passes.

**Target:** 16 clients, at least 15 at Tier A, median time to first successful tool call <= 120 seconds.

---

## 1. Support tiers

| Tier | Meaning | Requirements |
|---|---|---|
| **A** | Auto-configured and verified | Detect, back up, write, handshake, one real tool call, rollback test |
| **B** | Auto-detected, guided | Detect and print the exact command; no config write |
| **C** | Documented recipe | HTTP/SDK integration, no local config |

A Tier A client must pass on Windows 11, macOS 14+, and Ubuntu 24.04. If it passes on one OS only, it is Tier B on the others.

---

## 2. The 16 clients

| # | Client | Kind | Config mechanism | Key / command | Transport | Today | 0.4.0 target |
|---|---|---|---|---|---|---|---|
| 1 | Claude Code | CLI agent | `claude mcp add` (user scope) | `claude mcp add --scope user jiro -- jiro mcp` | stdio | no | A (if CLI present) |
| 2 | Claude Desktop | Desktop app | `claude_desktop_config.json` | `mcpServers.jiro` | stdio | A | A |
| 3 | OpenAI Codex CLI | CLI agent | `~/.codex/config.toml` | `mcp.servers.jiro` | stdio | A | A |
| 4 | Cursor | IDE | `~/.cursor/mcp.json` | `servers.jiro` | stdio | A | A |
| 5 | Windsurf | IDE | `mcp_config.json` | `mcpServers.jiro` | stdio | no | A |
| 6 | Cline | VS Code extension | `cline_mcp_settings.json` in globalStorage | `mcpServers.jiro` | stdio | no | A |
| 7 | Roo Code | VS Code extension | `mcp_settings.json` in globalStorage | `mcpServers.jiro` | stdio | no | A |
| 8 | Kilo Code | VS Code extension | `mcp_settings.json` in globalStorage | `mcpServers.jiro` | stdio | no | A |
| 9 | Continue.dev | IDE extension | `~/.continue/config.json` | `mcpServers.jiro` | stdio | A | A |
| 10 | Zed | Editor | `settings.json` | `mcpServers.jiro` | stdio | A | A |
| 11 | OpenCode | CLI/TUI | `~/.config/opencode/config.json` | `mcp.servers.jiro` | stdio | A | A |
| 12 | Gemini CLI | CLI agent | `~/.gemini/settings.json` | `mcpServers.jiro` | stdio | no | A |
| 13 | Goose | CLI agent | `~/.config/goose/config.yaml` | `extensions.jiro` | stdio | no | A |
| 14 | VS Code Copilot | IDE agent mode | workspace `.vscode/mcp.json` | `servers.jiro` | stdio/HTTP | no | A |
| 15 | OpenClaw | Agent runtime | `~/.openclaw/config.json` | `mcpServers.jiro` | stdio | A | A |
| 16 | Hermes | Agent runtime | `~/.hermes/config.json` | `mcpServers.jiro` | stdio | A | A |

"Today" reflects `jiro/mcp_setup.py` as of 0.3.3. The eight existing clients are the starting point, not the finish line.

> Config paths change between client versions. The registry stores a list of candidate paths and validates by content, not by hardcoded assumption. Every path is re-verified in the clean-VM matrix before release.

---

## 3. Framework adapters (not counted in the 16)

These are SDK-level integrations, not MCP clients. They ship as examples and typed wrappers:

| Framework | Path |
|---|---|
| LangChain | `JiroSearchTool` + retriever wrapper |
| LlamaIndex | `JiroReader` + query engine tool |
| OpenAI Agents SDK | function tools generated from OpenAPI |
| CrewAI | tool wrapper |
| AutoGen | tool wrapper |
| Mastra | TypeScript tool wrapper |
| n8n | HTTP node recipe |

They are Tier C. They do not affect the 15-client claim.

---

## 4. What `jiro setup` must do per client

1. **Detect.** Check binary on PATH, known config paths, extension directories, and app bundles.
2. **Back up.** Copy the existing config to `<name>.jiro-backup-<timestamp>`.
3. **Merge.** Read the existing file, add or update only the JIRO entry, preserve everything else, preserve comments where the format allows.
4. **Validate.** Parse the written file with the format's parser. A syntactically invalid write is a failure.
5. **Verify.** Start `jiro mcp`, perform the MCP initialize handshake, call `plan_search` with a trivial query, and assert a graph id comes back.
6. **Report.** Print a table: client, path, tier, verification, next step.
7. **Record.** Write `~/.jiro/setup-manifest.json` with hashes of every file touched.
8. **Rollback.** `jiro setup --uninstall --client X` restores the backup byte-for-byte.

### Non-negotiable rules

- Never rewrite an entire config to add one key.
- Never write secrets; use `env` references.
- Never require admin/sudo.
- Never touch a file outside the registry's allowlist.
- Never claim success without the handshake and tool call.
- Always support `--dry-run` and `--json`.

---

## 5. Shared sessions and transport

- **stdio** is the default for single-agent clients. `jiro mcp` runs in-process and uses local SQLite.
- **Streamable HTTP** is required for shared sessions, multi-agent handoff, and team deployments. `jiro serve --mcp-http`.
- A stdio client can still join a shared session if a local server is running; the stdio server proxies to `http://127.0.0.1:8080` with a scoped token.
- If no server is running, the client gets a local-only session. The tool response says so explicitly.

### Session scopes

| Role | Can plan | Can search/fetch | Can verify | Can write | Can hand off |
|---|---|---|---|---|---|
| planner | yes | no | no | no | yes |
| retriever | no | yes | no | no | no |
| verifier | no | read-only | yes | no | yes |
| writer | no | no | read-only | yes | yes |
| observer | no | no | no | no | no |

Scopes are enforced at the tool boundary, not in prompts. This matches the Track A invariant: capabilities never come from content or model output.

---

## 6. Clean-VM acceptance test

For each client and OS:

1. Snapshot a clean VM with the client installed and never configured for JIRO.
2. Record the config file hash.
3. Run `jiro setup --client <id> --yes`.
4. Assert exit code 0 and a Tier A result.
5. Launch the client or its CLI equivalent.
6. Call `plan_search` with `{"query": "what is jiro"}`.
7. Assert a valid graph id and a non-empty plan.
8. Run `jiro setup --uninstall --client <id>`.
9. Assert the config hash matches the snapshot.
10. Record time from command start to step 7.

Artifacts: `docs/v0.4.0/benchmarks/setup.json` with per-client, per-OS results and timings.

---

## 7. Known constraints

- **Claude Code** is CLI-managed. We can invoke `claude mcp add`, but we do not edit its internal config directly.
- **VS Code Copilot** MCP support depends on the VS Code version and the agent mode setting. If the version is too old, degrade to Tier B with a guided upgrade note.
- **Cline / Roo / Kilo** store config under VS Code `globalStorage`, whose path varies by VS Code variant (Stable, Insiders, VSCodium). The registry must check all variants.
- **Goose** uses YAML. Merge must preserve comments and anchors; if the parser cannot preserve them, fail closed and print a manual snippet instead of rewriting.
- **Windows paths** with spaces and OneDrive redirection are common. Expand environment variables and test both `%APPDATA%` and `%USERPROFILE%`.
- **stdio clients** cannot share sessions across processes without the local HTTP server. Say this in the tool output, not in a footnote.

---

## 8. Why this matters

Most "MCP-ready" projects ship one JSON snippet and call it integration. The failure mode is always the same: the user edits the wrong file, the client silently ignores the server, and the first tool call never happens.

The 0.4.0 standard is different: detection, merge, parse validation, handshake, real call, rollback. That is what makes "one command, 15 agents" a testable claim instead of a launch line.
