"""Read-only, explicit lifecycle resolution for annotated raw evidence.

This is not semantic inference or authorization. SHA-256 detects incomplete
records, not malicious edits. Unannotated Markdown keeps its legacy behavior.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


ENTRY_START = re.compile(r"^##\s+(.+?)\s*$")
ENTRY_META = re.compile(r"^<!-- dsh-entry:\s*(.*?)\s*-->\s*$")
HASH = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class RawEntry:
    path: Path
    line: int
    title: str
    text: str
    entry_id: str | None
    kind: str | None
    supersedes: str | None
    verified: bool
    issue: str | None = None

    @property
    def key(self) -> tuple[str, int]:
        return (str(self.path), self.line)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate-metadata-key")
        result[key] = value
    return result


def parse_raw_entries(path: Path, text: str) -> tuple[str, list[RawEntry]]:
    """Return the legacy prefix and complete annotated entry-sized blocks.

    Only an H2 immediately followed by a dsh-entry marker starts a record.
    Subheadings in a body remain part of that body. Invalid markers/hashes
    cannot participate in supersession and are exposed only in history mode.
    """
    raw_lines = text.splitlines(keepends=True)
    lines = [line.rstrip("\r\n") for line in raw_lines]
    starts = [i for i in range(len(lines) - 1)
              if ENTRY_START.match(lines[i]) and lines[i + 1].startswith("<!-- dsh-entry:")]
    if not starts:
        return text, []
    entries: list[RawEntry] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(lines)
        block = "\n".join(lines[start:end])
        title = ENTRY_START.match(lines[start]).group(1)
        entry_id = kind = supersedes = None
        issue = None
        try:
            match = ENTRY_META.fullmatch(lines[start + 1])
            if not match:
                raise ValueError("malformed-metadata")
            metadata = json.loads(match.group(1), object_pairs_hook=_unique_object)
            if not isinstance(metadata, dict):
                raise ValueError("malformed-metadata")
            entry_id = metadata.get("id")
            kind = metadata.get("type")
            supersedes = metadata.get("supersedes")
            if not isinstance(entry_id, str) or not entry_id.strip():
                raise ValueError("invalid-id")
            if kind not in {"fact", "correction", "tombstone"}:
                raise ValueError("invalid-type")
            if supersedes is not None and (not isinstance(supersedes, str) or not supersedes.strip()):
                raise ValueError("invalid-supersedes")
            if kind in {"correction", "tombstone"} and not supersedes:
                raise ValueError("missing-supersedes")
            if kind == "fact" and supersedes:
                raise ValueError("fact-cannot-supersede")
            expected = metadata.get("body_sha256")
            body = "".join(raw_lines[start + 2:end]).strip()
            if not isinstance(expected, str) or not HASH.fullmatch(expected):
                raise ValueError("missing-body-hash")
            if not body or hashlib.sha256(body.encode("utf-8")).hexdigest() != expected:
                raise ValueError("body-hash-mismatch")
        except (ValueError, TypeError) as exc:
            issue = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else "malformed-metadata"
        entries.append(RawEntry(path, start + 1, title, block,
                                entry_id if isinstance(entry_id, str) else None,
                                kind if isinstance(kind, str) else None,
                                supersedes if isinstance(supersedes, str) else None,
                                issue is None, issue))
    return "\n".join(lines[:starts[0]]), entries


def transaction_exclusions(vault: Path, entries: list[RawEntry]) -> tuple[set[tuple[str, int]], int]:
    """Reject entries linked to a known uncommitted/incomplete transaction.

    A raw export without transaction manifests is supported. An unreadable
    manifest makes the raw snapshot uncertain: fail closed for annotated raw
    records, without touching legacy/curated Markdown or changing any file.
    """
    by_target_id: dict[tuple[str, str], list[RawEntry]] = defaultdict(list)
    for entry in entries:
        by_target_id[(str(entry.path.resolve()), entry.entry_id or "")].append(entry)
    excluded: set[tuple[str, int]] = set()
    invalid_manifests = 0
    for path in sorted((vault / "memory" / ".vault-transactions").glob("*/manifest.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("operations", []), list):
                raise ValueError("invalid manifest")
            for op in data.get("operations", []):
                if not isinstance(op, dict) or op.get("kind") != "raw_append":
                    continue
                if data.get("state") in {"committed", "committed_recovered"}:
                    continue
                target_id = (str(Path(str(op.get("target", ""))).resolve()), str(op.get("entry_id", "")))
                excluded.update(entry.key for entry in by_target_id.get(target_id, []))
        except (OSError, ValueError, TypeError):
            invalid_manifests += 1
    if invalid_manifests:
        excluded.update(entry.key for entry in entries)
    return excluded, invalid_manifests


def resolve_lifecycle(entries: list[RawEntry], excluded: set[tuple[str, int]] | None = None
                      ) -> tuple[dict[tuple[str, int], str], dict[str, int]]:
    """Resolve only unique, verified explicit edges; never infer equivalence.

    IDs are scoped to the selected vault's raw corpus. Duplicates, cycles,
    competing corrections and dangling targets are diagnosed conservatively.
    A later correction cannot revive an older superseded fact by deleting its
    replacement: all unambiguous edges are retained, not only live sources.
    """
    excluded = excluded or set()
    status = {entry.key: "active" for entry in entries}
    counts = Counter(entry.entry_id for entry in entries if entry.entry_id)
    usable: dict[str, RawEntry] = {}
    diagnostics: Counter[str] = Counter()
    # A marker copied inside a raw body can look like a new record. If any
    # annotated record in a file is invalid, its boundaries are not trustworthy:
    # no entry from that file may authorize suppression of other evidence.
    ambiguous_paths = {entry.path for entry in entries if not entry.verified}
    if ambiguous_paths:
        diagnostics["ambiguous_files"] = len(ambiguous_paths)
    for entry in entries:
        if not entry.verified:
            status[entry.key] = "invalid"
            diagnostics["invalid"] += 1
        elif entry.key in excluded:
            status[entry.key] = "uncommitted"
            diagnostics["uncommitted"] += 1
        elif entry.path in ambiguous_paths:
            status[entry.key] = "ambiguous-file"
        elif counts[entry.entry_id] != 1:
            status[entry.key] = "ambiguous-id"
            diagnostics["duplicate_ids"] += 1
        else:
            usable[entry.entry_id] = entry
    edges: dict[str, str] = {}
    for key, entry in usable.items():
        if not entry.supersedes:
            continue
        if entry.supersedes not in usable:
            status[entry.key] = "unresolved-target"
            diagnostics["unresolved_targets"] += 1
        else:
            edges[key] = entry.supersedes
    # Iterative traversal avoids recursion limits on long correction chains.
    cyclic: set[str] = set()
    done: set[str] = set()
    for origin in edges:
        trail: list[str] = []
        seen: dict[str, int] = {}
        cursor = origin
        while cursor in edges and cursor not in done:
            if cursor in seen:
                cyclic.update(trail[seen[cursor]:])
                break
            seen[cursor] = len(trail)
            trail.append(cursor)
            cursor = edges[cursor]
        done.update(trail)
    targets: Counter[str] = Counter(edges.values())
    effective_targets: set[str] = set()
    for source, target in edges.items():
        if source in cyclic or target in cyclic:
            status[usable[source].key] = "ambiguous-cycle"
            diagnostics["cycle_edges"] += 1
        elif targets[target] > 1:
            status[usable[source].key] = "ambiguous-conflict"
            diagnostics["conflicting_edges"] += 1
        else:
            effective_targets.add(target)
    # Apply accepted edges last, independent of file/entry iteration order.
    for target in effective_targets:
        status[usable[target].key] = "superseded"
    for entry in entries:
        if entry.kind == "tombstone" and status[entry.key] == "active":
            status[entry.key] = "tombstone"
    return status, dict(sorted(diagnostics.items()))
