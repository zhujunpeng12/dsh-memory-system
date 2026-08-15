from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

import bootstrap


class BootstrapTests(unittest.TestCase):
    def test_recent_events_use_three_nonempty_days_and_h2_only(self) -> None:
        original_memory = bootstrap.MEMORY
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events"
            events.mkdir()
            fixtures = {
                "2026-08-14.md": "# 日期\n## 最新事件\n### 内部细节\n",
                "2026-08-12.md": "# 日期\n## " + "长标题" * 80 + "\n",
                "2026-08-07.md": "# 日期\n## 上周事件\n",
                "2026-08-01.md": "# 日期\n## 超出窗口\n",
            }
            for name, content in fixtures.items():
                (events / name).write_text(content, encoding="utf-8")
            bootstrap.MEMORY = root
            try:
                result = bootstrap.recent_event_index(today=date(2026, 8, 14))
            finally:
                bootstrap.MEMORY = original_memory

        self.assertIn("2026-08-14", result)
        self.assertIn("2026-08-12", result)
        self.assertIn("2026-08-07", result)
        self.assertNotIn("2026-08-01", result)
        self.assertNotIn("内部细节", result)
        self.assertLessEqual(len(result.encode("utf-8")), 2100)
        for line in result.splitlines():
            if line.startswith("## "):
                self.assertLessEqual(len(line.encode("utf-8")), 180)

    def test_instruction_audit_deduplicates_siblings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dsh_home = root / ".dsh"
            project = root / "project"
            dsh_home.mkdir()
            project.mkdir()
            (project / ".git").mkdir()
            (dsh_home / "AGENTS.md").write_text("global", encoding="utf-8")
            (project / "AGENTS.md").write_text("same", encoding="utf-8")
            (project / "CLAUDE.md").write_text("same", encoding="utf-8")
            result = bootstrap.instruction_budget_audit(project, dsh_home)

        self.assertIn("2 个文件", result)
        self.assertIn("渲染包装与热包未计", result)


if __name__ == "__main__":
    unittest.main()
