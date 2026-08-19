"""Explainable cold recall for the DSH Markdown Vault.

The first lane is intentionally lexical and dependency-free: exact/title/path
matches plus Chinese bigram BM25 and metadata reranking.  Vector retrieval is
reported as disabled until a real recall benchmark proves it is needed.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from recall_trigger import has_recall_subject, should_cold_recall, trigger_reasons

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


from vault_path import vault_root

VAULT = vault_root()
HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$")
ASCII_RE = re.compile(r"[a-z0-9][a-z0-9_.:/-]*", re.I)
HAN_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
DATE_RE = re.compile(r"^(20\d{2})-(\d{2})-(\d{2})")
EMBEDDED_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/](?:[^ \t\r\n\"']*[\\/])*"
    r"|\\\\[^ \t\r\n\"']*[\\/](?:[^ \t\r\n\"']*[\\/])*)"
)
TRAILING_EXT_RE = re.compile(r"\.(?:md|txt|json|html|py|csv)\b", re.I)
RANKING_STOP_TOKENS = frozenset({
    "继续", "上次", "之前", "以前", "历史", "证据", "来源", "路径", "召回", "触发",
    "验证", "说明", "是否", "收到", "列出", "不要", "修改", "任何", "文件", "写入", "只读",
    "本轮", "上下", "下文", "回答", "重复", "注入", "打开", "正文", "调用", "工具", "复测",
    "冷召", "召回", "前三", "trace", "files/chunks/candidates/selected/elapsed",
})


@dataclass
class Chunk:
    path: Path
    source_type: str
    title: str
    text: str
    line: int


@dataclass
class Hit:
    path: str
    source_type: str
    title: str
    line: int
    score: float
    reasons: list[str]
    snippet: str


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def tokenize(text: str) -> list[str]:
    value = normalize(text)
    tokens = ASCII_RE.findall(value)
    for run in HAN_RUN_RE.findall(value):
        if len(run) > 1:
            tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
        else:
            tokens.append(run)
        if 1 < len(run) <= 8:
            tokens.append(run)
    return tokens


def strip_embedded_paths(text: str) -> str:
    """Remove embedded absolute/UNC path prefixes so location tokens
    (drive letters, Users, Desktop) do not dominate relevance scoring."""
    stripped = EMBEDDED_PATH_RE.sub(" ", text)
    return TRAILING_EXT_RE.sub(" ", stripped)


def ranking_tokens(text: str) -> list[str]:
    """Keep trigger/control language out of relevance scoring."""
    return [token for token in tokenize(text) if token not in RANKING_STOP_TOKENS]


def source_type(path: Path, vault: Path) -> str:
    rel = path.resolve().relative_to(vault.resolve())
    parts = {part.casefold() for part in rel.parts}
    if path.name == "rules.md":
        return "rules"
    if "projects" in parts:
        return "toolmap" if "toolmap" in path.name.casefold() else "project"
    if "index" in parts:
        return "index"
    if path.name.endswith(".raw.md"):
        return "raw"
    return "event"


def discover_files(vault: Path, include_raw: bool = False) -> list[Path]:
    memory = vault / "memory"
    candidates: list[Path] = []
    rules = memory / "rules.md"
    if rules.is_file():
        candidates.append(rules)
    for folder in (memory / "events", memory / "index"):
        if folder.exists():
            candidates.extend(folder.glob("*.md"))
    projects = vault / "projects"
    if projects.exists():
        candidates.extend(projects.rglob("*.md"))
    result: list[Path] = []
    for path in candidates:
        if not path.is_file():
            continue
        if path.name.endswith(".raw.md") and not include_raw:
            continue
        if path.name.startswith(".") or ".tmp-" in path.name or ".vault-transactions" in path.parts:
            continue
        result.append(path)
    return sorted(set(result), key=lambda item: str(item).casefold())


def _split_large(chunk: Chunk, max_bytes: int = 2400) -> list[Chunk]:
    if len(chunk.text.encode("utf-8")) <= max_bytes:
        return [chunk]
    pieces: list[Chunk] = []
    lines = chunk.text.splitlines()
    current: list[str] = []
    current_line = chunk.line
    used = 0
    for offset, line in enumerate(lines):
        cost = len((line + "\n").encode("utf-8"))
        if current and used + cost > max_bytes:
            pieces.append(Chunk(chunk.path, chunk.source_type, chunk.title, "\n".join(current), current_line))
            current = []
            used = 0
            current_line = chunk.line + offset
        current.append(line)
        used += cost
    if current:
        pieces.append(Chunk(chunk.path, chunk.source_type, chunk.title, "\n".join(current), current_line))
    return pieces


def parse_chunks(path: Path, vault: Path) -> list[Chunk]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        # 非 UTF-8/损坏文件：跳过该文件而不是崩掉整个召回
        return []
    stype = source_type(path, vault)
    headings: dict[int, str] = {}
    chunks: list[Chunk] = []
    body: list[str] = []
    start_line = 1

    def flush() -> None:
        if not body:
            return
        text = "\n".join(body).strip()
        if not text:
            return
        title = " > ".join(headings[level] for level in sorted(headings)) or path.stem
        chunks.extend(_split_large(Chunk(path, stype, title, text, start_line)))

    for number, line in enumerate(lines, start=1):
        match = HEADING_RE.match(line)
        if match:
            flush()
            level = len(match.group(1))
            headings[level] = match.group(2).strip()
            for stale in [key for key in headings if key > level]:
                headings.pop(stale, None)
            body = [line]
            start_line = number
        else:
            if not body:
                start_line = number
            body.append(line)
    flush()
    return chunks


def bm25_scores(chunks: list[Chunk], query_tokens: list[str]) -> list[float]:
    if not chunks:
        return []
    counters = [Counter(tokenize(chunk.title + "\n" + chunk.text)) for chunk in chunks]
    lengths = [sum(counter.values()) for counter in counters]
    avg_length = sum(lengths) / max(1, len(lengths))
    document_frequency: Counter[str] = Counter()
    for counter in counters:
        document_frequency.update(counter.keys())
    scores = [0.0] * len(chunks)
    k1, b = 1.5, 0.75
    for term in set(query_tokens):
        df = document_frequency.get(term, 0)
        if not df:
            continue
        idf = math.log(1.0 + (len(chunks) - df + 0.5) / (df + 0.5))
        for index, counter in enumerate(counters):
            frequency = counter.get(term, 0)
            if not frequency:
                continue
            denominator = frequency + k1 * (1 - b + b * lengths[index] / max(1.0, avg_length))
            scores[index] += idf * frequency * (k1 + 1) / denominator
    return scores


def project_names(cwd: Path) -> set[str]:
    try:
        current = cwd.resolve()
    except OSError:
        current = cwd
    return {part.name.casefold() for part in (current, *current.parents) if part.name}


def metadata_score(
    chunk: Chunk,
    query: str,
    query_tokens: list[str],
    cwd_projects: set[str],
    vault: Path,
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    query_norm = normalize(query)
    title_norm = normalize(chunk.title)
    text_norm = normalize(chunk.text)
    try:
        path_norm = normalize(str(chunk.path.resolve().relative_to(vault.resolve())))
    except (OSError, ValueError):
        path_norm = normalize(chunk.path.name)
    if len(query_norm) >= 2 and query_norm in text_norm:
        score += 4.0
        reasons.append("exact-body")
    if len(query_norm) >= 2 and query_norm in title_norm:
        score += 5.0
        reasons.append("exact-title")
    title_hits = sum(1 for token in set(query_tokens) if token in title_norm)
    if title_hits:
        score += min(3.0, title_hits * 0.45)
        reasons.append(f"title-token×{title_hits}")
    path_hits = sum(1 for token in set(query_tokens) if len(token) > 1 and token in path_norm)
    if path_hits:
        score += min(1.5, path_hits * 0.35)
        reasons.append(f"path-token×{path_hits}")
    base = {"rules": 0.8, "project": 0.65, "toolmap": 0.5, "index": 0.35, "event": 0.15, "raw": -0.25}
    score += base.get(chunk.source_type, 0)
    reasons.append(f"source={chunk.source_type}")
    if chunk.source_type in {"project", "toolmap"}:
        try:
            project_root = chunk.path.resolve().relative_to((vault / "projects").resolve()).parts[0].casefold()
        except (OSError, ValueError, IndexError):
            project_root = ""
        if project_root and project_root in cwd_projects:
            score += 1.2
            reasons.append("cwd-project")
    if chunk.source_type in {"event", "raw"}:
        match = DATE_RE.match(chunk.path.name)
        if match:
            try:
                event_date = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
                age = max(0, (date.today() - event_date).days)
                recency = 0.6 * math.exp(-age / 30)
                score += recency
                reasons.append(f"recency={recency:.2f}")
            except ValueError:
                pass
    return score, reasons


def clip_utf8(text: str, limit: int) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    suffix = "…"
    raw = raw[: max(0, limit - len(suffix.encode("utf-8")))]
    while raw:
        try:
            return raw.decode("utf-8") + suffix
        except UnicodeDecodeError:
            raw = raw[:-1]
    return suffix


def snippet(chunk: Chunk, query: str, query_tokens: list[str], limit: int = 760) -> str:
    text = chunk.text.strip()
    normalized = normalize(text)
    probes = [normalize(query)] + sorted(set(query_tokens), key=len, reverse=True)
    position = next((normalized.find(probe) for probe in probes if probe and normalized.find(probe) >= 0), 0)
    start = max(0, position - 180)
    sample = ("…" if start else "") + text[start : start + 700]
    return clip_utf8(sample.replace("\x00", ""), limit)


def recall(
    query: str,
    *,
    vault: Path = VAULT,
    cwd: Path | None = None,
    include_raw: bool = False,
    top: int = 8,
) -> tuple[list[Hit], dict[str, object]]:
    started = time.perf_counter()
    files = discover_files(vault, include_raw=include_raw)
    chunks = [chunk for path in files for chunk in parse_chunks(path, vault)]
    clean_query = strip_embedded_paths(query)
    query_tokens = ranking_tokens(clean_query)
    lexical = bm25_scores(chunks, query_tokens)
    cwd_projects = project_names(cwd or Path.cwd())
    ranked: list[tuple[float, Chunk, list[str]]] = []
    for chunk, bm25 in zip(chunks, lexical):
        extra, reasons = metadata_score(chunk, clean_query, query_tokens, cwd_projects, vault)
        lexical_match = bm25 > 0 or any(
            reason.startswith(("exact-", "title-token", "path-token"))
            for reason in reasons
        )
        if not lexical_match:
            continue
        score = bm25 + extra
        if bm25 > 0:
            reasons.insert(0, f"bm25={bm25:.2f}")
        if score > 0.25:
            ranked.append((score, chunk, reasons))
    ranked.sort(key=lambda item: (-item[0], str(item[1].path).casefold(), item[1].line))
    hits: list[Hit] = []
    per_file: defaultdict[str, int] = defaultdict(int)
    for score, chunk, reasons in ranked:
        key = str(chunk.path.resolve()).casefold()
        if per_file[key] >= 2:
            continue
        per_file[key] += 1
        hits.append(Hit(
            path=str(chunk.path),
            source_type=chunk.source_type,
            title=chunk.title,
            line=chunk.line,
            score=round(score, 4),
            reasons=reasons,
            snippet=snippet(chunk, clean_query, query_tokens),
        ))
        if len(hits) >= top:
            break
    trace = {
        "mode": "exact+chinese-bigram-bm25+metadata",
        "vector": "disabled",
        "files": len(files),
        "chunks": len(chunks),
        "candidates": len(ranked),
        "selected": len(hits),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
    }
    return hits, trace


def render_packet(query: str, reasons: list[str], hits: list[Hit], trace: dict[str, object], max_bytes: int) -> str:
    header = (
        "# [vault-cold-recall] DSH 冷层召回包\n"
        f"查询：{query}\n"
        f"触发：{', '.join(reasons) if reasons else 'manual-force'}\n"
        f"模式：{trace['mode']}；vector={trace['vector']}；只对本轮有效\n"
    )
    output = header
    for index, hit in enumerate(hits, start=1):
        block = (
            f"\n## {index}. {hit.title}\n"
            f"来源：{hit.path}:{hit.line}\n"
            f"类型/分数：{hit.source_type} / {hit.score:.3f}\n"
            f"命中：{', '.join(hit.reasons)}\n\n"
            f"{hit.snippet}\n"
        )
        footer = (
            f"\n[trace files={trace['files']} chunks={trace['chunks']} candidates={trace['candidates']} "
            f"selected={index} elapsed={trace['elapsed_ms']}ms；完整正文沿来源路径读取]\n"
        )
        if len((output + block + footer).encode("utf-8")) > max_bytes:
            break
        output += block
    footer = (
        f"\n[trace files={trace['files']} chunks={trace['chunks']} candidates={trace['candidates']} "
        f"selected={output.count('来源：')} elapsed={trace['elapsed_ms']}ms；完整正文沿来源路径读取]\n"
    )
    return clip_utf8(output + footer, max_bytes)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Explainable cold recall for the DSH Markdown Vault")
    parser.add_argument("--query", required=True)
    parser.add_argument("--cwd", default=str(Path.cwd()))
    parser.add_argument("--max-bytes", type=int, default=4200)
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--include-raw", action="store_true")
    parser.add_argument("--force", action="store_true", help="run even when automatic trigger policy does not match")
    parser.add_argument("--decide-only", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    # Windows 控制台下 argv 可能带孤立代理（GBK 解码 UTF-8 payload 产物），
    # 会在下游 clip_utf8 严格 UTF-8 编码处崩溃——入口做一次 replace 往返剥离。
    args.query = args.query.encode("utf-8", "replace").decode("utf-8")
    args.cwd = args.cwd.encode("utf-8", "replace").decode("utf-8")
    reasons = trigger_reasons(args.query)
    decision = should_cold_recall(args.query)
    if args.decide_only:
        print(json.dumps({"trigger": decision, "reasons": reasons, "has_subject": has_recall_subject(args.query)}, ensure_ascii=False))
        return 0
    if not args.force and not decision:
        print(json.dumps({"trigger": False, "reasons": []}, ensure_ascii=False) if args.json else "[vault-cold-recall: skipped] no trigger")
        return 0
    hits, trace = recall(
        args.query,
        vault=VAULT,
        cwd=Path(args.cwd),
        include_raw=args.include_raw,
        top=max(1, min(args.top, 20)),
    )
    if args.json:
        print(json.dumps({
            "trigger": True,
            "reasons": reasons or ["manual-force"],
            "trace": trace,
            "hits": [asdict(hit) for hit in hits],
        }, ensure_ascii=False, indent=2))
    else:
        print(render_packet(args.query, reasons, hits, trace, max(1000, args.max_bytes)), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
