# JIRO v0.4.0 — One-Command Install

**Goal:** a new user goes from zero to a verified MCP tool call in one command, on Windows, macOS, or Linux, without editing a config file.

**Measured target:** median time to first successful tool call <= 120 seconds on a clean VM. See `CLAIMS.md`.

---

## 1. Primary command

True one command (installs the pinned package, then configures clients):

```bash
npx @jiro/setup@0.4.0
```

Python-native one line (same result, two steps joined):

```bash
uv tool install "jirosearch==0.4.0" && jiro setup --all --yes
```

Why `uv tool`:
- isolated environment, no project pollution;
- the `jiro` binary lands on PATH;
- pinned version, reproducible;
- uninstall is one command.

Fallbacks:

```bash
pipx install "jirosearch==0.4.0" && jiro setup --all
python -m pip install --user "jirosearch==0.4.0" && jiro setup --all
```

Node users:

```bash
npx @jiro/setup@0.4.0
```

Docker:

```bash
docker run --rm -p 8080:8080 ghcr.io/devanimecx/jiro:0.4.0
```

Windows PowerShell, checksum-verified release asset:

```powershell
$base = "https://github.com/DevAnimecx/jiro/releases/download/v0.4.0"
irm "$base/install.ps1" -OutFile install.ps1
irm "$base/install.ps1.sha256" -OutFile install.ps1.sha256
$expected = (Get-Content install.ps1.sha256).Split()[0].ToLower()
$actual = (Get-FileHash install.ps1 -Algorithm SHA256).Hash.ToLower()
if ($actual -ne $expected) { throw "checksum mismatch" }
./install.ps1 -Version 0.4.0
```

Do not pipe a remote script directly into a shell. Download, verify, run.

---

## 2. What the command does

`jiro setup --all --yes`:

1. Checks Python, platform, disk, and network.
2. Detects supported clients from `jiro/fabric/clients.py`.
3. Prints a table of detected / not detected / unsupported.
4. Backs up every config it will touch.
5. Merges only the JIRO entry into each config.
6. Parses the written file to prove it is valid.
7. Starts the MCP server and completes an initialize handshake.
8. Calls `plan_search` with a trivial query and asserts a graph id.
9. Writes `~/.jiro/setup-manifest.json`.
10. Prints next steps per client.

If any step fails for a client, that client is marked failed, its backup is restored, and the rest continue. The command exits non-zero only if zero clients succeed.

---

## 3. Flags

| Flag | Meaning |
|---|---|
| `--all` | Every detected client |
| `--client <id>` | One client (repeatable) |
| `--dry-run` | Detect and print the plan; write nothing |
| `--json` | Machine-readable result |
| `--yes` | No prompts |
| `--no-verify` | Skip handshake (not recommended; marks unverified) |
| `--uninstall` | Remove only JIRO entries and restore backups |
| `--transport stdio\|http` | Local stdio or shared HTTP session |
| `--server-url <url>` | Use an existing JIRO server |
| `--profile minimal\|full` | Minimal tools or full research surface |

---

## 4. After install

```bash
jiro doctor
jiro doctor --json
jiro serve --mcp-http
```

`jiro doctor` checks:
- binary and version
- config validity for every configured client
- MCP handshake
- one search
- one scrape
- packet generation
- parameter store read/write
- disk and cache
- network egress to the configured engines

`jiro doctor --fix` repairs only safe, reversible issues: stale PID, missing cache dir, invalid JSON written by an older JIRO version. It never overwrites unrelated config.

---

## 5. Uninstall

```bash
jiro setup --uninstall --all
uv tool uninstall jirosearch
```

The manifest lists every file touched and its backup hash. Uninstall:
- restores each backup byte-for-byte;
- removes the JIRO entry if the config changed after setup;
- leaves unrelated entries untouched;
- prints a per-file result.

If a backup is missing, uninstall fails closed and prints the exact entry to remove manually. It does not guess.

---

## 6. Security rules

- **Pinned version.** No `latest` in the primary path.
- **Checksums.** Release assets publish SHA256; the installer verifies before execution.
- **No sudo.** Per-user install only.
- **No telemetry.** Opt-in only, off by default.
- **No secrets in configs.** API keys stay in environment variables or the OS keychain.
- **Path allowlist.** The writer only touches known client config paths.
- **Atomic writes.** Write to a temp file in the same directory, fsync, then replace.
- **Backups before writes.** Always.
- **Parse validation.** A config that cannot be parsed after write is restored immediately.
- **No remote code from web pages.** Setup never fetches instructions from a search result.

Supply chain:
- PyPI trusted publishing for `jirosearch`.
- Signed Git tags for releases.
- `pip-audit` and secret scan in CI.
- Reproducible wheel build where feasible.

---

## 7. Failure modes and messages

| Failure | Message | Action |
|---|---|---|
| Python too old | "JIRO 0.4.0 requires Python 3.11+" | Print install command for 3.12 |
| `uv` missing | "uv not found; falling back to pipx" | Continue with pipx |
| Client config unreadable | "Cannot parse <path>; skipped, no changes made" | Print manual snippet |
| Handshake timeout | "Server started but did not complete MCP initialize in 10s" | Print `jiro mcp --debug` output |
| Port in use | "8080 is busy; using 8081" | Persist chosen port in manifest |
| Disk full | "Need 200 MB; found 40 MB" | Stop before writing |
| Permission denied | "Cannot write <path>; rerun without admin or fix ACL" | Stop for that client only |
| Partial write | "Validation failed; backup restored" | Continue with other clients |

Every message names the file, the cause, and the next action.

---

## 8. Acceptance tests

`tests/test_fabric_setup.py`:
- merge into empty config
- merge into existing config with unrelated servers
- preserve comments (TOML/YAML where supported)
- invalid JSON before write -> no change
- failed validation -> backup restored
- uninstall restores byte-identical file
- dry-run writes nothing
- JSON output schema
- path allowlist rejects traversal
- concurrent setup does not corrupt config (file lock)

`benchmarks/setup.py`:
- 16 clients x 3 OS clean VMs
- median time to first call
- per-client pass/fail
- artifact `docs/v0.4.0/benchmarks/setup.json`

---

## 9. The one-command promise

The command is not "one command" because it is short. It is one command because it performs detection, merge, validation, handshake, real call, and rollback without asking the user to understand any of them.

That is the bar. If a client needs a manual step, it is Tier B, and the output says so before the user wastes time.
