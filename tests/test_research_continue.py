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
from dotclaw.tools.executor import ToolExecutor
from dotclaw.tools.registry import ToolRegistry


class ContinueFakeLLM:
    async def chat(self, messages, tools=None, purpose="chat", stream=False, journal=None):
        system_text = messages[0].content
        if "证据提取助手" in system_text:
            yield ChatChunk(
                content=json.dumps([{
                    "claim": "继续研究新增结论",
                    "evidence": "新增证据",
                    "relevance": "回应 follow-up",
                }], ensure_ascii=False),
                is_final=False,
            )
            yield ChatChunk(is_final=True, finish_reason="stop")
            return
        yield ChatChunk(content="## 结论\n包含继续研究内容 [N1]。", is_final=False)
        yield ChatChunk(is_final=True, finish_reason="stop")


async def continue_fetch_url(url: str, max_bytes: int = 200_000):
    return {
        "url": url,
        "title": "Fetched",
        "content_type": "text/html",
        "text": "新增网页正文证据。" * 80,
        "truncated": False,
    }


class TestResearchContinue(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.storage = ResearchStorage(Path(self._tmp.name) / "research")

    async def asyncTearDown(self):
        self._tmp.cleanup()

    async def test_continue_adds_follow_up_sources_notes_and_report(self):
        manager = ResearchManager(self.storage, llm=ContinueFakeLLM())

        with patch("dotclaw.research.manager._search_sync") as search_mock, \
             patch("dotclaw.research.manager.fetch_url", side_effect=continue_fetch_url):
            search_mock.return_value = {
                "query": "AI",
                "results": [{"title": "Old", "url": "https://example.com/old"}],
            }
            initial = await manager.run("AI", depth="quick")

            search_mock.return_value = {
                "query": "AI 国产大模型",
                "results": [
                    {"title": "Duplicate", "url": "https://example.com/old?utm_source=x"},
                    {"title": "New", "url": "https://example.com/new"},
                ],
            }
            continued = await manager.continue_research(
                initial["id"],
                "国产大模型",
                depth="quick",
            )

        task = self.storage.load_task(initial["id"])
        sources = self.storage.load_sources(initial["id"])
        notes = self.storage.load_notes(initial["id"])
        report = Path(continued["report_path"]).read_text(encoding="utf-8")

        self.assertEqual(continued["follow_up"]["status"], "done")
        self.assertEqual(continued["follow_up"]["added_sources"], 1)
        self.assertEqual(len(sources), 2)
        self.assertEqual(sources[-1].batch, "f1")
        self.assertTrue(any(note.batch == "f1" for note in notes))
        self.assertEqual(task.follow_ups[0]["question"], "国产大模型")
        self.assertIn("包含继续研究内容", report)

    async def test_research_continue_tool_registered_and_executes(self):
        manager = ResearchManager(self.storage, llm=ContinueFakeLLM())
        with patch("dotclaw.research.manager._search_sync") as search_mock, \
             patch("dotclaw.research.manager.fetch_url", side_effect=continue_fetch_url):
            search_mock.return_value = {
                "query": "AI",
                "results": [{"title": "Old", "url": "https://example.com/old"}],
            }
            initial = await manager.run("AI", depth="quick")
            search_mock.return_value = {
                "query": "AI 后续",
                "results": [{"title": "New", "url": "https://example.com/new"}],
            }

            registry = ToolRegistry()
            register_research_tools(registry, manager)
            executor = ToolExecutor(registry)
            result = await executor.execute(
                "research_continue",
                {"id": initial["id"], "follow_up": "后续", "depth": "quick"},
            )

        self.assertFalse(result.is_error)
        self.assertIn('"follow_up"', result.output)
        self.assertIn("research_continue", registry.all_names())


if __name__ == "__main__":
    unittest.main()
