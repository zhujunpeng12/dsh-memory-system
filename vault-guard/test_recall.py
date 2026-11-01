from __future__ import annotations

import tempfile
import hashlib
import json
import os
import subprocess
import sys
from unittest import mock
import unittest
from pathlib import Path

from recall import discover_files, recall, render_packet, ranking_tokens, strip_embedded_paths
from raw_lifecycle import parse_raw_entries, resolve_lifecycle
from vault_tx import VaultTransaction
from recall_trigger import should_cold_recall, trigger_reasons


class RecallTriggerTests(unittest.TestCase):
    def test_acknowledgements_do_not_open_cold_memory(self) -> None:
        self.assertEqual(trigger_reasons("好的。"), [])
        self.assertEqual(trigger_reasons("ok"), [])

    def test_history_and_memory_entities_trigger_with_reasons(self) -> None:
        reasons = trigger_reasons("继续上次的记忆系统修复")
        self.assertIn("history-reference", reasons)
        self.assertIn("memory-entity", reasons)
        self.assertTrue(should_cold_recall("继续上次的记忆系统锁优化"))

    def test_meta_only_followup_does_not_open_cold_memory(self) -> None:
        query = "继续只读复测，请列出本轮 cold recall trace 的 files/chunks/candidates，不要调用工具"
        self.assertTrue(trigger_reasons(query))
        self.assertFalse(should_cold_recall(query))


class ColdRecallTests(unittest.TestCase):
    def make_vault(self, root: Path) -> Path:
        vault = root / "Obsidian Vault"
        events = vault / "memory" / "events"
        index = vault / "memory" / "index"
        projects = vault / "projects"
        events.mkdir(parents=True)
        index.mkdir(parents=True)
        projects.mkdir(parents=True)
        (vault / "memory" / "rules.md").write_text(
            "# 规则\n\n## 风控阈值必须验证\n任何仓位变更都要先核对最大回撤。\n",
            encoding="utf-8",
        )
        (events / "2026-08-14.md").write_text(
            "# 事件\n\n## 修复热记忆锁\n完成短租约、心跳和异常恢复。\n",
            encoding="utf-8",
        )
        (events / "2026-08-14.raw.md").write_text(
            "# raw\n\n## 私有原始轨迹\nraw-only-secret-token\n",
            encoding="utf-8",
        )
        (index / "2026-08.md").write_text(
            "# 月索引\n\n## 风控决策\n指向规则中的风控阈值。\n",
            encoding="utf-8",
        )
        (projects / "alpha").mkdir()
        (projects / "beta").mkdir()
        for project in ("alpha", "beta"):
            (projects / project / "project.md").write_text(
                "# 共同项目\n\n## 数据口径\n项目代号 common-project-token。\n",
                encoding="utf-8",
            )
        return vault

    def test_raw_is_excluded_by_default_and_explicit_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = self.make_vault(Path(tmp))
            default = discover_files(vault)
            explicit = discover_files(vault, include_raw=True)
            self.assertFalse(any(path.name.endswith(".raw.md") for path in default))
            self.assertTrue(any(path.name.endswith(".raw.md") for path in explicit))

    def test_exact_and_chinese_bm25_recall_expected_rule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = self.make_vault(Path(tmp))
            hits, trace = recall("继续上次风控阈值验证", vault=vault, cwd=Path(tmp), top=6)
            self.assertTrue(hits)
            self.assertEqual(hits[0].source_type, "rules")
            self.assertIn("风控阈值", hits[0].title)
            self.assertEqual(trace["vector"], "disabled")
            self.assertIn("chinese-bigram-bm25", trace["mode"])

    def test_current_project_is_reranked_and_file_cap_is_two(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = self.make_vault(root)
            alpha_cwd = root / "work" / "alpha"
            alpha_cwd.mkdir(parents=True)
            hits, _ = recall("common-project-token", vault=vault, cwd=alpha_cwd, top=10)
            project_hits = [hit for hit in hits if hit.source_type == "project"]
            self.assertTrue(project_hits)
            self.assertIn("alpha", Path(project_hits[0].path).parts)
            beta_hits = [hit for hit in project_hits if "beta" in Path(hit.path).parts]
            self.assertTrue(beta_hits)
            self.assertTrue(all("cwd-project" not in hit.reasons for hit in beta_hits))
            counts: dict[str, int] = {}
            for hit in hits:
                counts[hit.path] = counts.get(hit.path, 0) + 1
            self.assertLessEqual(max(counts.values()), 2)

    def test_rendered_packet_obeys_utf8_budget_and_cites_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = self.make_vault(Path(tmp))
            query = "继续上次记忆系统锁修复与风控阈值"
            reasons = trigger_reasons(query)
            hits, trace = recall(query, vault=vault, cwd=Path(tmp), top=8)
            packet = render_packet(query, reasons, hits, trace, max_bytes=4096)
            self.assertLessEqual(len(packet.encode("utf-8")), 4096)
            self.assertIn("[vault-cold-recall]", packet)
            self.assertIn("完整正文沿来源路径读取", packet)
            self.assertNotIn("raw-only-secret-token", packet)

    def test_vault_root_name_does_not_match_every_candidate_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = self.make_vault(Path(tmp))
            hits, trace = recall(
                "继续上次 Vault 记忆系统验证，不要修改任何文件",
                vault=vault,
                cwd=Path(tmp),
                top=8,
            )
            self.assertTrue(hits)
            self.assertLess(trace["candidates"], trace["chunks"])
            self.assertFalse(all(any(reason.startswith("path-token") for reason in hit.reasons) for hit in hits))


class EmbeddedPathStrippingTests(ColdRecallTests):
    def test_windows_path_prefix_is_stripped_to_subject(self) -> None:
        clean = strip_embedded_paths(r"C:\Users\someone\Desktop\dsh记忆系统v3交接说明.md")
        self.assertEqual(clean.strip(), "dsh记忆系统v3交接说明")
        tokens = ranking_tokens(clean)
        self.assertNotIn("users", tokens)
        self.assertNotIn("desktop", tokens)
        self.assertNotIn("someone", tokens)
        self.assertIn("dsh", tokens)
        self.assertIn("记忆系统", tokens)

    def test_urls_are_not_stripped(self) -> None:
        clean = strip_embedded_paths("https://example.com/复盘/指引")
        self.assertIn("https", clean)
        self.assertIn("example.com", clean)

    def test_unc_path_prefix_is_stripped(self) -> None:
        clean = strip_embedded_paths(r"\\server\share\策略文档.md")
        self.assertEqual(clean.strip(), "策略文档")

    def test_path_location_tokens_do_not_dominate_recall(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = self.make_vault(Path(tmp))
            desk = vault / "projects" / "desktop-tool"
            desk.mkdir()
            (desk / "project.md").write_text(
                "# 桌面复盘工具\n\nsource: C:\\Users\\someone\\Desktop\\复盘\\\n",
                encoding="utf-8",
            )
            hits, _ = recall(
                r"C:\Users\someone\Desktop\热记忆锁修复",
                vault=vault,
                cwd=Path(tmp),
                top=6,
            )
            self.assertTrue(hits)
            self.assertFalse(any("desktop-tool" in hit.path for hit in hits))
            self.assertIn("热记忆锁", hits[0].title)


class RawLifecycleTests(unittest.TestCase):
    def entry(self, entry_id, body, *, kind="fact", supersedes=None, title="记忆记录"):
        metadata = {"id": entry_id, "type": kind, "supersedes": supersedes,
                    "body_sha256": hashlib.sha256(body.strip().encode("utf-8")).hexdigest()}
        return f"## {title}\n<!-- dsh-entry: {json.dumps(metadata, ensure_ascii=False)} -->\n{body.strip()}\n\n"

    def make_raw(self, root, text, day="2026-08-15"):
        path = root / "memory" / "events" / f"{day}.raw.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# raw\n\n" + text, encoding="utf-8")
        return path

    def test_corrections_filter_before_ranking_budget_and_keep_audit_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            raw = self.make_raw(vault, self.entry("old", "风控阈值是百分之三。", title="旧风控阈值"))
            self.make_raw(vault, self.entry("new", "风控阈值是百分之二。", kind="correction", supersedes="old", title="新风控阈值"), "2026-08-16")
            before = raw.read_bytes()
            hits, trace = recall("风控阈值", vault=vault, include_raw=True, top=1)
            self.assertEqual(len(hits), 1)
            self.assertIn("百分之二", hits[0].snippet)
            self.assertEqual(trace["lifecycle_excluded_chunks"], 1)
            self.assertNotIn("百分之三", render_packet("风控阈值", [], hits, trace, 1100))
            history, audit = recall("风控阈值", vault=vault, include_raw=True, include_history=True)
            self.assertTrue(any("百分之三" in hit.snippet and "raw-lifecycle=superseded" in hit.reasons for hit in history))
            self.assertEqual(audit["raw_mode"], "history")
            self.assertEqual(raw.read_bytes(), before)

    def test_tombstone_and_long_chain_do_not_resurrect_older_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            blocks = [self.entry("a", "唯一主题 最早事实")]
            blocks += [self.entry(f"n{i}", "唯一主题 修订事实", kind="correction", supersedes="a" if i == 0 else f"n{i-1}") for i in range(1050)]
            blocks += [self.entry("deleted", "唯一主题 不再使用", kind="tombstone", supersedes="n1049")]
            self.make_raw(vault, "".join(blocks))
            hits, trace = recall("唯一主题", vault=vault, include_raw=True)
            self.assertEqual(hits, [])
            self.assertEqual(trace["lifecycle_excluded_chunks"], 1052)

    def test_duplicate_cycle_dangling_and_fork_do_not_hide_facts(self):
        cases = {
            "duplicate": [self.entry("a", "主题原文"), self.entry("a", "重复原文"), self.entry("b", "修订", kind="correction", supersedes="a")],
            "cycle": [self.entry("a", "主题原文", kind="correction", supersedes="b"), self.entry("b", "修订", kind="correction", supersedes="a")],
            "dangling": [self.entry("a", "主题原文", kind="correction", supersedes="missing")],
            "fork": [self.entry("a", "主题原文"), self.entry("b", "修订", kind="correction", supersedes="a"), self.entry("c", "另一修订", kind="correction", supersedes="a")],
        }
        for label, blocks in cases.items():
            with self.subTest(label=label):
                _, entries = parse_raw_entries(Path("example.raw.md"), "".join(blocks))
                states, diagnostics = resolve_lifecycle(entries)
                self.assertNotIn("superseded", states.values())
                self.assertTrue(diagnostics)

    def test_invalid_hash_or_incomplete_transaction_cannot_supersede(self):
        for invalid in ("hash", "transaction", "manifest"):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as tmp:
                vault = Path(tmp)
                old = self.entry("a", "主题原文")
                correction = self.entry("b", "主题修订", kind="correction", supersedes="a")
                if invalid == "hash":
                    correction = correction.replace("主题修订", "被截断")
                raw = self.make_raw(vault, old + correction)
                if invalid in {"transaction", "manifest"}:
                    manifest = vault / "memory" / ".vault-transactions" / "failed" / "manifest.json"
                    manifest.parent.mkdir(parents=True)
                    manifest.write_text("broken" if invalid == "manifest" else json.dumps({
                        "state": "incomplete_preserved",
                        "operations": [{"kind": "raw_append", "target": str(raw), "entry_id": "b"}],
                    }), encoding="utf-8")
                hits, trace = recall("主题", vault=vault, include_raw=True)
                if invalid == "manifest":
                    self.assertEqual(hits, [])
                    self.assertEqual(trace["lifecycle_diagnostics"]["invalid_manifests"], 1)
                else:
                    self.assertTrue(any("主题原文" in hit.snippet for hit in hits))
                    self.assertFalse(any("主题修订" in hit.snippet for hit in hits))
                self.assertTrue(trace["lifecycle_diagnostics"])

    def test_embedded_metadata_cannot_authorize_supersession(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            self.make_raw(vault, self.entry("old", "主题原文"))
            fake = self.entry("fake", "主题伪纠正", kind="correction", supersedes="old")
            # A legitimate raw body can quote an entry-looking block. Without
            # conservative boundary handling the inner marker could hide old.
            self.make_raw(vault, self.entry("outer", "引用材料\n" + fake), "2026-08-16")
            hits, trace = recall("主题", vault=vault, include_raw=True)
            self.assertTrue(any("主题原文" in hit.snippet for hit in hits))
            self.assertEqual(trace["lifecycle_diagnostics"]["ambiguous_files"], 1)
            self.assertFalse(any("raw-lifecycle=active" in hit.reasons and "主题伪纠正" in hit.snippet for hit in hits))

    def test_graph_status_is_independent_of_record_order(self):
        a = self.entry("a", "原文")
        b = self.entry("b", "修订一", kind="correction", supersedes="a")
        c = self.entry("c", "修订二", kind="correction", supersedes="a")
        d = self.entry("d", "后续修订", kind="correction", supersedes="b")
        for order in ([a, b, c, d], [d, c, b, a]):
            _, entries = parse_raw_entries(Path("example.raw.md"), "".join(order))
            states, _ = resolve_lifecycle(entries)
            by_id = {entry.entry_id: states[entry.key] for entry in entries}
            self.assertEqual(by_id["a"], "active")
            self.assertEqual(by_id["b"], "superseded")
            self.assertEqual(by_id["c"], "ambiguous-conflict")
            self.assertEqual(by_id["d"], "active")

    def test_body_headings_and_large_record_preserve_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            body = "主题原文\n### 子标题\n" + "正文内容\n" * 700
            self.make_raw(vault, self.entry("a", body) + self.entry("b", "主题新值", kind="correction", supersedes="a"))
            hits, trace = recall("主题", vault=vault, include_raw=True)
            self.assertTrue(any("主题新值" in hit.snippet for hit in hits))
            self.assertFalse(any("主题原文" in hit.snippet for hit in hits))
            self.assertGreater(trace["lifecycle_excluded_chunks"], 1)

    def test_legacy_and_default_recall_are_unchanged_and_never_scan_raw(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            self.make_raw(vault, "## 旧式原文\nlegacy-topic\n")
            curated = vault / "memory" / "events" / "2026-08-15.md"
            curated.write_text("## 提炼结论\ncurated-topic\n", encoding="utf-8")
            with mock.patch("recall.transaction_exclusions", side_effect=AssertionError("raw scan forbidden")):
                hits, trace = recall("curated-topic", vault=vault)
            self.assertTrue(hits)
            self.assertEqual(trace["raw_mode"], "excluded")
            raw_hits, _ = recall("legacy-topic", vault=vault, include_raw=True)
            self.assertTrue(raw_hits)

    def test_writer_roundtrip_and_cli_audit_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            raw = vault / "memory" / "events" / "2026-08-15.raw.md"
            for entry_id, body, kind, target in [("a", "主题旧值\r\nWindows 换行内容", "fact", None), ("b", "主题新值", "correction", "a")]:
                with VaultTransaction(vault=vault, purpose="fixture") as tx:
                    tx.append_raw_entry(raw, title="主题", body=body, source="test", entry_id=entry_id,
                                        entry_type=kind, supersedes=target)
            hits, _ = recall("主题", vault=vault, include_raw=True)
            self.assertEqual(len(hits), 1)
            self.assertIn("主题新值", hits[0].snippet)
            cli = str(Path(__file__).parent / "recall.py")
            env = {**os.environ, "MEMORY_VAULT": str(vault)}
            bad = subprocess.run([sys.executable, cli, "--query", "主题", "--include-history"], env=env, capture_output=True)
            self.assertEqual(bad.returncode, 2)
            result = subprocess.run([sys.executable, cli, "--query", "主题", "--force", "--include-raw", "--include-history", "--json"], env=env, capture_output=True, text=True, encoding="utf-8", check=True)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["trace"]["raw_mode"], "history")
            self.assertEqual(len(payload["hits"]), 2)


if __name__ == "__main__":
    unittest.main()
