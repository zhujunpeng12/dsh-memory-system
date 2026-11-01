# Explicit raw-memory lifecycle

## What changed, and what did not

`recall.py --include-raw` now uses an effective view of **annotated raw entries** before BM25 scoring, per-file limits, top-k selection and packet budgeting. A valid `correction` suppresses its explicit `supersedes` target. A valid `tombstone` suppresses its target and is not itself returned as a current fact. Suppression never deletes or edits the underlying evidence.

Default recall still excludes `.raw.md`; ordinary curated events, rules and project Markdown remain unchanged. This release does **not** automatically retire an old curated summary, infer that two sentences contradict each other, or add semantic/vector retrieval. Project context remains a ranking preference, not an access-control boundary.

```sh
# Current effective raw evidence, explicitly opted in
python vault-guard/recall.py --query '风控阈值' --force --include-raw
# Audit: includes superseded, tombstone and invalid entries, with status labels
python vault-guard/recall.py --query '风控阈值' --force --include-raw --include-history --json
```

The plugin exposes the same options as `includeRaw` and `includeHistory`. History requires explicit raw inclusion; it does not implicitly open raw data. Audit results are historical evidence, **not a statement that all returned records remain true**.

## Record format and integrity

The existing writer already emits an H2 title immediately followed by a `dsh-entry` JSON comment containing `id`, `type`, `supersedes` and `body_sha256`. No migration or new storage service is required. The resolver verifies the whole stripped body against its SHA-256, preserving CRLF within a body. Body subheadings and large chunks keep their parent entry identity.

IDs are scoped to the selected Vault's raw corpus. Explicit unique IDs may link records across different daily raw files. There is no inferred project/path scope, cross-Vault lookup or matching of similar text. Unannotated legacy raw Markdown retains its prior behavior; it has no automatic lifecycle identity.

SHA-256 detects truncated or altered bytes; **it does not authenticate an author or defend against an attacker who can rewrite a Vault and recompute hashes**. Filesystem access control, user approval for writes and source trust remain separate responsibilities.

## Conservative ambiguity rules

- Invalid metadata, missing/wrong body hashes and entries linked to known uncommitted/incomplete transactions cannot suppress anything. Effective recall excludes those annotated entries; history mode shows their status.
- An unreadable/malformed transaction manifest makes annotated raw status uncertain, so effective recall excludes annotated raw entries until the manifest can be inspected. Curated and unannotated Markdown are unaffected.
- Duplicate IDs, cycles, competing corrections and missing targets do not silently discard original evidence. The resolver reports diagnostics and labels ambiguous/unresolved entries. It does not choose a winner by file order or timestamp.
- A raw body can quote text that resembles a new entry marker. If any annotated record in a file fails verification, boundaries in that file are considered ambiguous: entries from it cannot authorize suppression. This can conservatively retain older evidence after a truncated append; diagnostics tell the caller not to treat it as a conflict-free answer.
- Deleting the latest correction does not resurrect an older superseded fact. All accepted explicit edges are retained during resolution, even when their source is itself superseded.
- This is a read-only snapshot. A concurrent append may yield temporary integrity/transaction diagnostics; no lock takeover, repair or write occurs during recall. Retry after a writer completes if appropriate.

Text packets expose raw mode, exclusion count and diagnostics. JSON trace includes `chunks_before_lifecycle`, `lifecycle_excluded_chunks` and `lifecycle_diagnostics`; individual results carry a `raw-lifecycle=...` reason. Original file/line citations remain available. This does not make unannotated legacy raw records integrity-verified.

## Tests and evaluation

Regression tests cover writer round-trips, CRLF, multiline/large bodies, long correction chains, tombstones, repeated IDs, cycles, forks, dangling targets, partial/invalid transactions, quoted metadata, file-order independence, default raw exclusion, audit flags and UTF-8 packet budgets. All tests use temporary synthetic Vaults.

The [offline retrieval evaluation](recall-evaluation.md) intentionally keeps a paraphrase miss and a stale curated-summary case visible. Do not present its ten handcrafted examples as real-user accuracy or proof that memory is solved.

## Design references and licensing

These references informed **concepts**, not copied source code, prompts or benchmark data:

- [Mem0 history storage](https://github.com/mem0ai/mem0/blob/main/mem0/memory/storage.py) and [explicit lifecycle operations](https://github.com/mem0ai/mem0/blob/main/mem0/configs/prompts.py): distinguish update/delete history from effective memory. Mem0 is Apache-2.0; this implementation does not use its model-driven decisions or database.
- [Letta MemFS](https://docs.letta.com/concepts/memfs) and [letta-code](https://github.com/letta-ai/letta-code): file-backed memory and separation of active context from retained history. The referenced code is Apache-2.0; this project remains Python standard library plus Markdown.
- [Basic Memory benchmarks](https://github.com/basicmachines-co/basic-memory/tree/main/benchmarks): evaluate retrieval separately from generated answers. Its repository is AGPL-3.0; no code, fixtures or dataset were copied. Our diagnostic fixtures and implementation are original and remain under this repository's MIT license.

Links describe the reviewed upstream concepts, not a claim of API compatibility or endorsement.
