from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from recall import discover_files, recall, render_packet, ranking_tokens, strip_embedded_paths
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


if __name__ == "__main__":
    unittest.main()
