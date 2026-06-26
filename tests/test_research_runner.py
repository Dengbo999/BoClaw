from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotclaw.llm.base import ChatChunk
from dotclaw.research import ResearchManager, ResearchRunner, ResearchStorage
from dotclaw.research.models import ResearchLimits, ResearchStatus


class RunnerFakeLLM:
    async def chat(self, messages, tools=None, purpose="chat", stream=False, journal=None):
        system_text = messages[0].content
        if "证据提取助手" in system_text:
            yield ChatChunk(
                content=json.dumps([{
                    "claim": "来源支持结论",
                    "evidence": "证据片段",
                    "relevance": "相关",
                }], ensure_ascii=False),
                is_final=False,
            )
            yield ChatChunk(is_final=True, finish_reason="stop")
            return
        yield ChatChunk(content="## 结论\n后台研究完成 [1]。", is_final=False)
        yield ChatChunk(is_final=True, finish_reason="stop")


async def fake_fetch_url(url: str, max_bytes: int = 200_000):
    await asyncio.sleep(0)
    return {
        "url": url,
        "title": f"Title {url[-1]}",
        "content_type": "text/html",
        "text": f"网页 {url[-1]} 正文，包含研究证据。",
        "truncated": False,
    }


async def slow_fetch_url(url: str, max_bytes: int = 200_000):
    await asyncio.sleep(5)
    return {
        "url": url,
        "title": "Slow",
        "content_type": "text/html",
        "text": "slow body",
        "truncated": False,
    }


def slow_search_sync(query: str, max_results: int):
    time.sleep(0.5)
    return {
        "query": query,
        "results": [{"title": "A", "url": "https://example.com/a"}],
    }


class TestResearchRunner(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.storage = ResearchStorage(Path(self._tmp.name) / "research")

    async def asyncTearDown(self):
        self._tmp.cleanup()

    async def test_start_returns_id_and_background_task_finishes(self):
        manager = ResearchManager(self.storage, llm=RunnerFakeLLM())
        runner = ResearchRunner(manager)

        with patch("dotclaw.research.manager._search_sync") as search_mock, \
             patch("dotclaw.research.manager.fetch_url", side_effect=fake_fetch_url):
            search_mock.return_value = {
                "query": "问题",
                "results": [{"title": "A", "url": "https://example.com/a"}],
            }
            started = await runner.start("问题", depth="quick")
            self.assertEqual(started["status"], "created")
            self.assertTrue(started["is_running"])

            for _ in range(50):
                status = await runner.status(started["id"])
                if status["status"] == "done":
                    break
                await asyncio.sleep(0.01)

        self.assertEqual(status["status"], "done")
        self.assertFalse(status["is_running"])
        self.assertTrue(status["has_report"])
        self.assertEqual(status["progress"]["message"], "研究完成")

    async def test_list_includes_recent_task_summary(self):
        manager = ResearchManager(self.storage, llm=RunnerFakeLLM())
        runner = ResearchRunner(manager)
        task = await manager.create_task("列表问题", depth="quick")

        items = await runner.list()

        self.assertEqual(items[0]["id"], task.id)
        self.assertEqual(items[0]["query"], "列表问题")
        self.assertFalse(items[0]["is_running"])

    async def test_cancel_marks_running_task_cancelled(self):
        manager = ResearchManager(
            self.storage,
            llm=RunnerFakeLLM(),
            limits=ResearchLimits(max_sources=1),
        )
        runner = ResearchRunner(manager)

        with patch("dotclaw.research.manager._search_sync") as search_mock, \
             patch("dotclaw.research.manager.fetch_url", side_effect=slow_fetch_url):
            search_mock.return_value = {
                "query": "问题",
                "results": [{"title": "A", "url": "https://example.com/a"}],
            }
            started = await runner.start("问题", depth="quick")
            await asyncio.sleep(0.05)
            cancelled = await runner.cancel(started["id"])

        self.assertTrue(cancelled["cancelled"])
        self.assertEqual(cancelled["status"], "cancelled")
        task = self.storage.load_task(started["id"])
        self.assertEqual(task.status, ResearchStatus.CANCELLED)

    async def test_shutdown_cancels_running_tasks(self):
        manager = ResearchManager(
            self.storage,
            llm=RunnerFakeLLM(),
            limits=ResearchLimits(max_sources=1),
        )
        runner = ResearchRunner(manager)

        with patch("dotclaw.research.manager._search_sync") as search_mock, \
             patch("dotclaw.research.manager.fetch_url", side_effect=slow_fetch_url):
            search_mock.return_value = {
                "query": "问题",
                "results": [{"title": "A", "url": "https://example.com/a"}],
            }
            started = await runner.start("问题", depth="quick")
            await asyncio.sleep(0.05)
            await runner.shutdown()

        task = self.storage.load_task(started["id"])
        self.assertEqual(task.status, ResearchStatus.CANCELLED)

    async def test_background_search_does_not_block_event_loop(self):
        manager = ResearchManager(self.storage, llm=RunnerFakeLLM())
        runner = ResearchRunner(manager)

        with patch("dotclaw.research.manager._search_sync", side_effect=slow_search_sync), \
             patch("dotclaw.research.manager.fetch_url", side_effect=fake_fetch_url):
            started = await runner.start("问题", depth="quick")
            started_at = time.perf_counter()
            await asyncio.sleep(0.01)
            elapsed = time.perf_counter() - started_at

            await runner.cancel(started["id"])

        self.assertLess(elapsed, 0.2)

    async def test_read_and_extract_save_progress_incrementally(self):
        manager = ResearchManager(self.storage, llm=RunnerFakeLLM())

        with patch("dotclaw.research.manager._search_sync") as search_mock, \
             patch("dotclaw.research.manager.fetch_url", side_effect=fake_fetch_url):
            search_mock.return_value = {
                "query": "问题",
                "results": [
                    {"title": "A", "url": "https://example.com/a"},
                    {"title": "B", "url": "https://example.com/b"},
                ],
            }
            result = await manager.run("问题", depth="quick")

        sources = self.storage.load_sources(result["id"])
        notes = self.storage.load_notes(result["id"])
        task = self.storage.load_task(result["id"])

        self.assertEqual(len(sources), 2)
        self.assertEqual(len(notes), 2)
        self.assertEqual(task.progress["current_stage"], "done")
        self.assertEqual(task.note_count, 2)


if __name__ == "__main__":
    unittest.main()
