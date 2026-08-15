# dsh-vault-memory — Persistent memory infrastructure for DeepSeek Harness

> Local-first persistent memory for DeepSeek Harness (DSH) agents: bounded hot-memory bootstrap, explainable cold recall, lease-locked transactional writes, read-only governance, and trajectory review. Pure Python + Markdown. No database, no vector service, no external dependencies.

**Important boundary: this repository contains only the mechanism, never any personal data.** Memory content (profile, rules, events, project notes) always stays in the user's own Obsidian Vault, referenced through environment variables.

## Why

Agent sessions are amnesic by default. This system turns "memory" into an engineering loop with six layers:

1. **Hot-memory bootstrap** — one ≤14KB packet injected per new session (gate status + instruction budget + user profile + active core rules + project summary + recent event headings)
2. **Working path** — follow global/project `AGENTS.md` rules to complete tasks
3. **Cold recall** — two-gate trigger (history reference + concrete topic), exact + Chinese bigram BM25 + metadata rerank, ≤4.2KB packet, vector retrieval off by default (zero deps)
4. **Authorized writes** — lease lock (30s lease / 5s heartbeat / stale-lock recovery) + multi-file transactions (before-image / SHA-256 precondition / manifest / receipt); raw entries are append-only; corrections require `supersedes`
5. **Slow governance** — `govern.py` read-only scan for duplicates, conflicts, staleness, size, rule lifecycle; never writes
6. **Trajectory review** — read-only scan of session traces using user corrections as the hard signal

## Highlights

| Capability | Description |
|---|---|
| Write safety | Single-writer lease lock + recoverable transactions, atomic multi-file changes, append-only raw |
| Chinese recall | Bigram BM25 + exact/title/path match + metadata rerank, fully explainable trace |
| Byte budgets | 14KB hot / 4.2KB cold hard budgets, UTF-8-safe clipping |
| Read-only governance | L0-L3 boundaries; collects evidence, never auto-deletes |
| Zero dependencies | Python stdlib + Markdown; Windows/macOS/Linux |

## Install as a DSH plugin (recommended)

The plugin form registers memory capabilities directly as agent tools:

```bash
dsh plugin add dsh-vault-memory        # once published
# or local:
npm pack && dsh plugin add ./dsh-vault-memory-0.1.0.tgz
```

Restart the Harness. The agent gains 5 tools:

| Tool | Purpose | Writes |
|---|---|---|
| `memory_bootstrap` | ≤14KB hot-memory packet | read-only |
| `memory_recall` | cold recall (exact + Chinese BM25) | read-only |
| `memory_gate` | mechanical gate checks | read-only |
| `memory_govern` | governance candidate scan | read-only |
| `memory_write` | authorized transactional write (dry-run default) | confirmed |

`memory_write` only previews by default; it applies only with `apply=true` after explicit user confirmation (enforced via the `tools/pre-execute` hook). All tools locate your vault via `MEMORY_VAULT` / `DSH_HOME`.

> Optional manual hooks: see `hooks.example.json` to attach `hook-first-prompt.py` to `UserPromptSubmit` for automatic hot-packet injection on the first prompt of every session.

## Quick start (standalone)

### 1. Create a memory vault

Copy the sanitized skeleton (10 minutes):

```bash
cp -r templates/vault/* "$HOME/Documents/Obsidian Vault/"
mv "$HOME/Documents/Obsidian Vault/memory/user_profile.example.md" "$HOME/Documents/Obsidian Vault/memory/user_profile.md"
mv "$HOME/Documents/Obsidian Vault/memory/rules.example.md" "$HOME/Documents/Obsidian Vault/memory/rules.md"
```

### 2. Configure environment

| Variable | Default | Meaning |
|---|---|---|
| `MEMORY_VAULT` | `~/Documents/Obsidian Vault` | Vault root (must contain `memory/` and `projects/`) |
| `DSH_HOME` | `~/.dsh` | DSH home (hooks config, storages) |

### 3. Generate the hot packet

```bash
python vault-guard/bootstrap.py --cwd /path/to/project --max-bytes 14000
```

### 4. Cold recall

```bash
python vault-guard/recall.py --query "continue the previous X" --cwd /path/to/project --force
```

## Repository layout

```
├── index.js                    # DSH host plugin: 5 memory tools + write guard
├── package.json                # npm package (dsh-vault-memory)
├── dsh.plugin.json             # DSH plugin manifest
├── cordis.patch.yml            # DSH bundle patch
├── vault-guard/                # Python core (bootstrap/recall/gate/govern/write/...)
├── templates/                  # sanitized memory-vault skeleton
├── hooks.example.json          # DSH hooks example
├── .env.example
└── LICENSE                     # MIT
```

## Run tests

```bash
cd vault-guard && python -m unittest discover -p "test_*.py" -v
# or from repo root:
npm run check
```

All tests use temporary directories; they never touch a real vault.

## Methodology (the philosophy)

- **Single source of truth**: one fact, one home (runtime params → AGENTS.md; decisions → project notes; daily flow → events; cross-project lessons → rules). Everywhere else holds pointers only.
- **Write authorization**: preview first, `--apply` only with explicit consent; raw append-only; corrections via `supersedes`.
- **Governance is read-only**: `govern.py` collects evidence and suggestions; promotion/archive/deletion always requires a human.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security issues: see [SECURITY.md](SECURITY.md).

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

- This repository contains no personal data; memory content stays on the user's machine.
- Review your vault layout, hooks wiring, and security boundaries before deploying.
