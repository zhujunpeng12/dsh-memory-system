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

## System architecture (six-layer closed loop)

```mermaid
flowchart TD
    Start([New session / task]) --> Hook[SessionStart Hook<br/>parse JSON + cwd · UTF-8 safe]

    Hook -->|solid: script execution| B1[① Hot-memory bootstrap<br/>bootstrap.py · bounded ≤14KB]
    B1 --> B1a[Mechanical gate<br/>gaps · lock · sync]
    B1 --> B1b[Instruction budget<br/>8KB/32KB/48KB soft warning]
    B1 --> B1c[User profile<br/>full inject · not project-bound]
    B1 --> B1d[Active rules<br/>star + citation count · budget-filtered]
    B1 --> B1e[Project summary<br/>cwd ancestor matches vault]
    B1 --> B1f[Recent event days<br/>14-day lookback · headings only · ≤180B each]
    B1f -->|single merged inject| B1out["[vault-bootstrap] packet"]

    B1out --> B2[② Working path<br/>AGENTS core · skill routing · minimal edits]
    B2 --> B2out[Verified delivery<br/>syntax/config/real-run/UI evidence]

    B2 -.on-demand read.-> B3[③ Cold layer on demand<br/>full rules · historical events/raw · project notes · monthly index · session logs]

    B3 --> Q1{Persistent value and<br/>user approves archive?}
    Q1 -->|no| Q1no[Plain closing · no auto write<br/>session/disposed → check --closing]
    Q1 -->|yes| B4[④ Authorized write transaction<br/>acquire lock · append raw facts only<br/>distill to events/projects/rules · release]
    B4 --> B4out[Authorization gate<br/>check --closing --expect-write]

    B4out --> B5[⑤ Slow maintenance<br/>mechanical health: gaps/size/core sync<br/>human governance: promote/arbitrate/expire/delete]

    B2 -.session trace.-> B6[⑥ Trajectory review loop<br/>trajectory-review.py read-only candidates]
    B6 --> B6a[Evidence layer<br/>user correction = hard signal · tool ledger as clue only]
    B6a --> B6b{Human review<br/>scenario → error → root cause → precondition}
    B6b -->|repeats ≥3 times| B6c[Rule backfill<br/>graduate into rules-core]
    B6b -->|ordinary exploration failure| B6d[No distillation]
    B6b -->|approved distillation| B6e[raw → events<br/>keep conclusion + evidence pointer]
    B6c --> B1
    B6e --> B1
```

Legend: solid = scripted execution; dashed = on-demand read / human review; diamond = user authorization decision. The feedback loop distills rules and events back into the next session's hot memory.

## Install as a DSH plugin (recommended)

The plugin form registers memory capabilities directly as agent tools:

```bash
dsh plugin add dsh-vault-memory        # once published
# or local:
npm pack && dsh plugin add ./dsh-vault-memory-0.1.0.tgz
```

Restart the Harness. The agent gains 6 tools:

| Tool | Purpose | Writes |
|---|---|---|
| `memory_bootstrap` | ≤14KB hot-memory packet | read-only |
| `memory_recall` | cold recall (exact + Chinese BM25) | read-only |
| `memory_gate` | mechanical gate checks | read-only |
| `memory_govern` | governance candidate scan | read-only |
| `memory_trajectory_review` | trajectory review candidates (user corrections = hard signal) | read-only |
| `memory_write` | authorized transactional write (dry-run default) | confirmed |

`memory_write` only previews by default; it applies only with `apply=true` after explicit user confirmation (enforced via the `tools/pre-execute` hook). All tools locate your vault via `MEMORY_VAULT` / `DSH_HOME`.

**Trajectory review evidence layer (optional companion)**: `memory_trajectory_review` reads the tool-call ledger (`${DSH_HOME}/storages/tool-telemetry.json`). Install the companion plugin `plugins/evidence-ledger/` (zero inject) to accumulate per-session tool calls and errors automatically. Without it, the review still scans session logs for user-correction signals.

> Optional manual hooks: see `hooks.example.json` to attach `hook-first-prompt.py` to `UserPromptSubmit` for automatic hot-packet injection on the first prompt of every session.

## Six layers in detail (what each layer does)

### ① Hot-memory bootstrap — one bounded context per session

**What**: at session start, compress "what the agent most needs to know right now" into one ≤14KB packet and inject it once. No full-vault reads to get started.

**Contents**: mechanical gate status (gaps/lock/sync), instruction-budget audit, user profile, active core rules (star + citation filtered), current project summary (cwd-ancestor match), recent event headings (14-day lookback, ≤180B each).

**How**: `memory_bootstrap` tool, or `python vault-guard/bootstrap.py --cwd <dir> --max-bytes 14000`. Automatic via hooks.

### ② Working path — rules-driven execution (methodology, no script)

**What**: how the agent works, not a script: priority & permission boundaries (system/user > project AGENTS.md > global rules > cold docs), skill routing (named → quickref → graph fallback), minimal edits, root-cause investigation, verified delivery (syntax/config/real-run/UI evidence).

**Why no script**: this layer is behavioral policy carried by `AGENTS.md`. The repo ships `templates/` with example rules as a starting point.

### ③ Cold layer on demand — open details only when needed

**What**: when a task needs evidence or details, cold recall opens full sources (complete rules, historical events/raw, project notes, monthly index, session logs).

**Trigger**: two gates — a recall signal (history/last time/correction) **and** a concrete searchable topic, both required. Plain acknowledgements or meta-only follow-ups do not open cold memory.

**How**: `memory_recall` tool, or `python vault-guard/recall.py --query "<q>" --cwd <dir> --force`. Exact + Chinese bigram BM25 + metadata rerank, ≤4.2KB packet with sources and trace.

### ④ Authorized writes — write only when persistent value + user consent

**What**: only "substantive output" (code/doc changes, persistent decisions, user preferences, data conventions, acceptance criteria, standing agreements) with explicit user consent goes through the transactional writer: acquire lease lock → append raw (facts only) → distill into events/projects/rules → release lock.

**Safety**: 30s lease + 5s heartbeat single-writer lock; before-image + SHA-256 precondition + manifest + receipt multi-file transactions; raw append-only (corrections require supersedes); dry-run by default; `tools/pre-execute` confirmation enforced.

**How**: `memory_write` tool (`op=raw/replace/recover`, applies only with `apply=true`).

### ⑤ Slow maintenance — mechanical health checks + human governance

**What**: periodically (or when the vault feels unhealthy): mechanical checks (raw gaps, size limits, rules-core sync, lock state) and human governance (rule graduation, conflict arbitration, expiry archiving, deletion confirmation).

**Boundary**: `govern.py` collects evidence and suggestions only (duplicates/conflicts/staleness/size/lifecycle candidates), **never auto-deletes**. Promotion/archive/deletion always requires a human.

**How**: `memory_govern` tool, or `python vault-guard/govern.py --json --max-items 100`. Pair with `check.py` closing gates (`--closing` / `--closing --expect-write`).

### ⑥ Trajectory review — evidence-driven quality feedback loop

**What**: at wrap-up, scan the session trace for three kinds of items: errors (user corrections = hard signal; AI self-review is self-serving), repeated tool errors (only when frequent), and reusable lessons. Output candidates in the four-field template: scenario → error → root cause → precondition.

**Evidence**: user-correction signals from session logs + the tool-call ledger from the `evidence-ledger` plugin (which tools fail most). The ledger is a clue, never an automatic verdict.

**Loop**: after human review, ordinary exploration failures are not distilled; patterns repeating ≥3 times graduate into rules-core; approved distillations go raw → events keeping conclusion + evidence pointer — rules and events feed back into the next session's hot memory.

**How**: `memory_trajectory_review` tool, or `python vault-guard/trajectory-review.py --cwd <dir>`. Install `plugins/evidence-ledger/` for ledger data.

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
