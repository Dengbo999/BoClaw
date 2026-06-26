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


class QualityFakeLLM:
    async def chat(self, messages, tools=None, purpose="chat", stream=False, journal=None):
        system_text = messages[0].content
        if "证据提取助手" in system_text:
            yield ChatChunk(
                content=json.dumps([{
                    "claim": "AI 投资持续增长",
                    "evidence": "AI 投资和部署正在增长",
                    "relevance": "说明当前 AI 现状",
                }], ensure_ascii=False),
                is_final=False,
            )
            yield ChatChunk(is_final=True, finish_reason="stop")
            return
        yield ChatChunk(content="## 结论\nAI 发展很快 [N1]。", is_final=False)
        yield ChatChunk(is_final=True, finish_reason="stop")


async def quality_fetch_url(url: str, max_bytes: int = 200_000):
    return {
        "url": url,
        "title": "Quality Source",
        "content_type": "text/html",
        "text": "AI 投资和部署正在增长。" * 80,
        "truncated": False,
    }


class TestResearchQuality(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.storage = ResearchStorage(Path(self._tmp.name) / "research")

    async def asyncTearDown(self):
        self._tmp.cleanup()

    async def test_sources_are_deduped_scored_and_grouped(self):
        manager = ResearchManager(self.storage, llm=QualityFakeLLM())

        with patch("dotclaw.research.manager._search_sync") as search_mock, \
             patch("dotclaw.research.manager.fetch_url", side_effect=quality_fetch_url):
            search_mock.return_value = {
                "query": "AI 现状",
                "results": [
                    {"title": "A", "url": "https://www.nature.com/articles/a?utm_source=x"},
                    {"title": "A duplicate", "url": "https://nature.com/articles/a"},
                    {"title": "B", "url": "https://example.gov/report"},
                ],
            }
            result = await manager.run("AI 现状", depth="quick")

        sources = self.storage.load_sources(result["id"])

        self.assertEqual(len(sources), 2)
        self.assertEqual(sources[0].domain, "nature.com")
        self.assertEqual(sources[0].canonical_url, "https://nature.com/articles/a")
        self.assertEqual(sources[0].credibility_label, "high")
        self.assertEqual(sources[1].domain, "example.gov")
        self.assertEqual(sources[1].credibility_label, "high")

    async def test_notes_get_citation_and_report_has_quality_section(self):
        manager = ResearchManager(self.storage, llm=QualityFakeLLM())

        with patch("dotclaw.research.manager._search_sync") as search_mock, \
             patch("dotclaw.research.manager.fetch_url", side_effect=quality_fetch_url):
            search_mock.return_value = {
                "query": "AI 现状",
                "results": [{"title": "A", "url": "https://example.com/a"}],
            }
            result = await manager.run("AI 现状", depth="quick")

        notes = self.storage.load_notes(result["id"])
        report = Path(result["report_path"]).read_text(encoding="utf-8")

        self.assertEqual(notes[0].citation_id, "N1")
        self.assertEqual(notes[0].confidence, "single_source")
        self.assertIn("## 可信度与限制", report)
        self.assertIn("可用来源少于 2 个", report)


if __name__ == "__main__":
    unittest.main()
