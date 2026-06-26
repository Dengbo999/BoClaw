from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotclaw.llm.base import ChatChunk
from dotclaw.research import ResearchManager, ResearchStorage
from dotclaw.tools.builtin import register_research_tools
from dotclaw.tools.registry import ToolRegistry


class FakeLLM:
    async def chat(self, messages, tools=None, purpose="chat", stream=False, journal=None):
        yield ChatChunk(content="## 结论\n基于来源 [1] 可以形成初步判断。", is_final=False)
        yield ChatChunk(is_final=True, finish_reason="stop")


async def fake_fetch_url(url: str, max_bytes: int = 200_000):
    return {
        "url": url,
        "title": "Fetched Title",
        "content_type": "text/html",
        "text": "这是网页正文，包含研究问题相关证据。",
        "truncated": False,
    }


class TestResearchManager(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.storage = ResearchStorage(Path(self._tmp.name) / "research")

    async def asyncTearDown(self):
        self._tmp.cleanup()

    async def test_run_saves_report_and_sources(self):
        manager = ResearchManager(self.storage, llm=FakeLLM())

        with patch("dotclaw.research.manager._search_sync") as search_mock, \
             patch("dotclaw.research.manager.fetch_url", side_effect=fake_fetch_url):
            search_mock.return_value = {
                "query": "测试问题",
                "results": [{"title": "Result", "url": "https://example.com/a"}],
            }
            result = await manager.run("测试问题", depth="quick")

        self.assertEqual(result["status"], "done")
        report_path = Path(result["report_path"])
        self.assertTrue(report_path.exists())
        self.assertIn("## 结论", report_path.read_text(encoding="utf-8"))

        task_path = report_path.parent / "task.json"
        sources_path = report_path.parent / "sources.json"
        self.assertTrue(task_path.exists())
        self.assertTrue(sources_path.exists())
        task = json.loads(task_path.read_text(encoding="utf-8"))
        self.assertEqual(task["status"], "done")
        sources = json.loads(sources_path.read_text(encoding="utf-8"))
        self.assertEqual(sources[0]["url"], "https://example.com/a")

    def test_register_research_tool(self):
        registry = ToolRegistry()
        register_research_tools(registry, research_mgr=object())
        self.assertIn("deep_research", registry.all_names())


if __name__ == "__main__":
    unittest.main()
