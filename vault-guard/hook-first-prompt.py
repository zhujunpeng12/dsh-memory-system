"""Inject one hot packet per session and bounded cold recall when warranted.

Why UserPromptSubmit instead of SessionStart:
- the local dsh-hooks bridge runs SessionStart detached, so it can miss turn 1;
- UserPromptSubmit is an awaited agent/pre-step waterfall;
- the bridge accepts event context only inside hookSpecificOutput with a matching
  hookEventName.

The DSH transcript/session marker is the durable once-per-session hot ledger.
Later prompts may still receive a temporary ``[vault-cold-recall]`` packet when
their text explicitly asks for history, evidence, correction or a memory entity.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from recall_trigger import should_cold_recall, trigger_reasons

try:
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DSH_HOME = Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh"))
HERE = Path(__file__).resolve().parent
GUARD_DIR = Path(os.environ.get("VAULT_GUARD_DIR") or HERE)
BOOTSTRAP = GUARD_DIR / "bootstrap.py"
RECALL = GUARD_DIR / "recall.py"
EVENT_NAME = "UserPromptSubmit"
SUCCESS_MARKER = "[vault-bootstrap] DSH 热记忆包"
SESSION_MARKERS = DSH_HOME / "storages" / "vault-bootstrap-sessions"


def extract_cwd(payload: object) -> str:
    keys = {"cwd", "current_dir", "working_directory", "project_dir"}
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in keys and isinstance(value, str) and value.strip():
                return value
        for value in payload.values():
            found = extract_cwd(value)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = extract_cwd(value)
            if found:
                return found
    return ""


def _text_value(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        pieces = [_text_value(item) for item in value]
        return "\n".join(piece for piece in pieces if piece).strip()
    if isinstance(value, dict):
        for key in ("text", "content", "value"):
            if key in value:
                text = _text_value(value[key])
                if text:
                    return text
    return ""


def extract_query(payload: object) -> str:
    """Extract only prompt-shaped fields; never concatenate the whole payload."""
    prompt_keys = ("prompt", "user_prompt", "userPrompt", "user_message", "userMessage", "message")
    if isinstance(payload, dict):
        for key in prompt_keys:
            if key in payload:
                text = _text_value(payload[key])
                if text:
                    return text
        for value in payload.values():
            text = extract_query(value)
            if text:
                return text
    elif isinstance(payload, list):
        for value in payload:
            text = extract_query(value)
            if text:
                return text
    return ""


def emit_context(text: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": EVENT_NAME,
            "additionalContext": text,
        }
    }, ensure_ascii=False))


def marker_path(payload: dict) -> Path | None:
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        return None
    digest = hashlib.sha256(session_id.encode("utf-8", errors="replace")).hexdigest()
    return SESSION_MARKERS / f"{digest}.json"


def marker_has_success(payload: dict) -> bool:
    path = marker_path(payload)
    if path is None or not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("success") is True
    except Exception:
        return False


def persist_success_marker(payload: dict) -> None:
    """Fallback ledger for Python environments without zstandard."""
    path = marker_path(payload)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Bounded cleanup prevents one small marker per session accumulating forever.
        cutoff = time.time() - 14 * 86400
        for old in path.parent.glob("*.json"):
            try:
                if old.stat().st_mtime < cutoff:
                    old.unlink()
            except OSError:
                pass
        tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
        try:
            tmp.write_text(json.dumps({
                "success": True,
                "at": time.time(),
                "transcript_path": payload.get("transcript_path"),
            }, ensure_ascii=False) + "\n", encoding="utf-8")
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
    except OSError:
        # Read-only hook sandboxes still retain transcript-based dedup when available.
        pass


def has_prior_success(payload: dict) -> bool:
    """Return whether a successful hook context already exists for this session."""
    if marker_has_success(payload):
        return True
    transcript = payload.get("transcript_path")
    if not isinstance(transcript, str) or not transcript.strip():
        return False
    path = Path(transcript)
    if not path.is_file():
        return False
    try:
        import zstandard as zstd

        with path.open("rb") as source, zstd.ZstdDecompressor().stream_reader(source) as reader:
            text = reader.read().decode("utf-8", errors="replace")
    except Exception:
        return False

    for raw_line in text.splitlines():
        try:
            event = json.loads(raw_line)
        except Exception:
            continue
        if event.get("type") != "user/message":
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        source = data.get("source")
        if not isinstance(source, dict) or source.get("kind") != "plugin" or source.get("plugin") != "hooks-claude-code":
            continue
        content = data.get("content")
        if not isinstance(content, list):
            continue
        if any(
            isinstance(block, dict)
            and isinstance(block.get("text"), str)
            and SUCCESS_MARKER in block["text"]
            for block in content
        ):
            return True
    return False


def run_command(command: list[str], timeout: int) -> tuple[str, str | None]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except Exception as exc:
        return "", str(exc)
    output = (result.stdout or "").strip()
    if result.returncode != 0 or not output:
        detail = (result.stderr or "无 stdout").strip()[:1000]
        return "", f"exit={result.returncode}：{detail}"
    return output, None


def main() -> int:
    try:
        raw_input = sys.stdin.read().lstrip("\ufeff")
        payload = json.loads(raw_input) if raw_input.strip() else {}
    except Exception as exc:
        emit_context(f"[vault-bootstrap: absent] UserPromptSubmit JSON 解析失败：{exc}")
        return 0

    if not isinstance(payload, dict):
        emit_context("[vault-bootstrap: absent] UserPromptSubmit payload 不是对象；下一轮将重试。")
        return 0

    cwd = extract_cwd(payload) or str(Path.cwd())
    query = extract_query(payload)
    reasons = trigger_reasons(query) if should_cold_recall(query) else []
    contexts: list[str] = []

    if not has_prior_success(payload):
        packet, error = run_command(
            [sys.executable, str(BOOTSTRAP), "--cwd", cwd, "--max-bytes", "14000"],
            timeout=40,
        )
        if error:
            contexts.append(
                f"[vault-bootstrap: absent] bootstrap {error}\n"
                "本次未标记，下一轮将重试；可按 AGENTS.md 手动兜底一次。"
            )
        else:
            persist_success_marker(payload)
            contexts.append(packet)

    if reasons:
        packet, error = run_command(
            [
                sys.executable,
                str(RECALL),
                "--query",
                query,
                "--cwd",
                cwd,
                "--max-bytes",
                "4200",
                "--force",
            ],
            timeout=20,
        )
        if error:
            contexts.append(f"[vault-cold-recall: absent] recall {error}；热包与本轮任务继续有效。")
        elif "[vault-cold-recall]" in packet:
            contexts.append(packet)

    if contexts:
        emit_context("\n\n".join(contexts))
    else:
        print("{}")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
