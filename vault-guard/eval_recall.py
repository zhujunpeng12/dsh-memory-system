"""Offline synthetic retrieval diagnostics, not a real-user quality benchmark."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

import recall as engine

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

FIXTURE = Path(__file__).resolve().parent.parent / "docs" / "recall-eval-fixture.json"


def load_fixture(path: Path) -> dict:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    if fixture.get("schema") != "dsh-recall-eval-v1" or fixture.get("synthetic") is not True:
        raise ValueError("expected explicitly synthetic dsh-recall-eval-v1 fixture")
    date.fromisoformat(fixture["as_of"])
    docs = fixture["documents"]
    ids = {doc["id"] for doc in docs}
    if len(ids) != len(docs) or len({case["id"] for case in fixture["cases"]}) != len(fixture["cases"]):
        raise ValueError("duplicate document/case ID")
    identities = {(doc["path"], doc["title"]) for doc in docs}
    if len(identities) != len(docs):
        raise ValueError("document path/title identities must be unique")
    for doc in docs:
        rel = Path(doc["path"])
        if rel.is_absolute() or ".." in rel.parts or "\n" in doc["title"]:
            raise ValueError("unsafe fixture path or title")
    for case in fixture["cases"]:
        references = set(case["expected"]) | set(case.get("forbidden", []))
        if case.get("preferred_first"):
            references.add(case["preferred_first"])
        if not references <= ids:
            raise ValueError("case references an unknown document")
        if set(case["expected"]) & set(case.get("forbidden", [])):
            raise ValueError("expected and forbidden evidence overlap")
        if case.get("cwd", "alpha") not in {"alpha", "beta"}:
            raise ValueError("fixture cwd must be alpha or beta")
    return fixture


def materialize(fixture: dict, vault: Path) -> dict:
    identities = {}
    for doc in fixture["documents"]:
        path = vault / doc["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        body = doc["body"].strip()
        metadata = ""
        if "raw" in doc:
            entry = {"id": doc["id"], "ts": 0, "source": "synthetic-evaluation",
                     "session_id": "synthetic", "supersedes": None, **doc["raw"],
                     "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest()}
            metadata = f"<!-- dsh-entry: {json.dumps(entry, ensure_ascii=False, sort_keys=True)} -->\n"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"## {doc['title']}\n{metadata}{body}\n\n")
        identities[(doc["path"], doc["title"])] = doc["id"]
    return identities


def summarize(rows: list[dict]) -> dict:
    answered = [row for row in rows if row["expected"]]
    no_answer = [row for row in rows if not row["expected"]]
    forbidden = [row for row in rows if row["forbidden"]]
    preferred = [row for row in rows if row["preferred_first"]]
    def metric(group, key):
        numerator = sum(bool(row[key]) for row in group)
        return {"count": numerator, "total": len(group),
                "rate": numerator / len(group) if group else None}
    return {
        "top3_evidence_hit": metric(answered, "evidence_hit"),
        "top3_forbidden_evidence_leak": metric(forbidden, "forbidden_leak"),
        "no_answer_false_recall": metric(no_answer, "false_recall"),
        "preferred_first_hit": metric(preferred, "preferred_first_hit"),
        "latency_ms_informational": {
            "median": statistics.median(row["elapsed_ms"] for row in rows) if rows else None,
            "max": max((row["elapsed_ms"] for row in rows), default=None),
        },
    }


def evaluate(fixture: dict) -> dict:
    rows = []
    with tempfile.TemporaryDirectory(prefix="dsh-recall-eval-") as tmp:
        root = Path(tmp)
        vault = root / "vault"
        identities = materialize(fixture, vault)
        # Freeze only retrieval's date-dependent ranking; elapsed time is real.
        with patch.object(engine, "date") as clock:
            clock.side_effect = date
            clock.today.return_value = date.fromisoformat(fixture["as_of"])
            for case in fixture["cases"]:
                cwd = root / "work" / case.get("cwd", "alpha")
                cwd.mkdir(parents=True, exist_ok=True)
                hits, trace = engine.recall(case["query"], vault=vault, cwd=cwd,
                                           include_raw=case.get("include_raw", False), top=3)
                retrieved = []
                for hit in hits:
                    rel = Path(hit.path).relative_to(vault).as_posix()
                    identity = identities.get((rel, hit.title.split(" > ")[-1]))
                    retrieved.append(identity or f"unmapped:{rel}:{hit.line}")
                expected, forbidden = case["expected"], case.get("forbidden", [])
                preferred = case.get("preferred_first")
                rows.append({
                    **case, "forbidden": forbidden, "preferred_first": preferred,
                    "retrieved": retrieved,
                    "evidence_hit": bool(set(expected) & set(retrieved)),
                    "forbidden_leak": bool(set(forbidden) & set(retrieved)),
                    "false_recall": not expected and bool(retrieved),
                    "preferred_first_hit": bool(retrieved and retrieved[0] == preferred),
                    "elapsed_ms": trace["elapsed_ms"],
                })
    return {"synthetic": True, "as_of": fixture["as_of"],
            "scope": "retrieval only; bypasses trigger policy, packet rendering and answer generation",
            "metrics": summarize(rows), "cases": rows}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = evaluate(load_fixture(args.fixture))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("SYNTHETIC retrieval diagnostics (not real-user accuracy)")
        for name, value in report["metrics"].items():
            print(f"{name}: {value}")
        for row in report["cases"]:
            print(f"{row['id']}: expected={row['expected']} retrieved={row['retrieved']} forbidden={row['forbidden']}")
    # Diagnostic failures are findings, not an arbitrary quality release gate.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
