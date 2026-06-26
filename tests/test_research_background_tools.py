from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotclaw.research import ResearchManager, ResearchRunner, ResearchStorage
from dotclaw.tools.builtin import register_research_tools
from dotclaw.tools.executor import ToolExecutor
from dotclaw.tools.registry import ToolRegistry


async def fake_fetch_url(url: str, max_bytes: int = 200_000):
    await asyncio.sleep(0)
    return {
        "url": url,
        "title": "Fetched",
        "content_type": "text/html",
        "text": "正文证据",
        "truncated": False,
    }


class TestResearchBackgroundTools(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.storage = ResearchStorage(Path(self._tmp.name) / "research")
        self.manager = ResearchManager(self.storage, llm=None)
        self.runner = ResearchRunner(self.manager)
        self.registry = ToolRegistry()
        register_research_tools(self.registry, self.manager, self.runner)
        self.executor = ToolExecutor(self.registry)

    async def asyncTearDown(self):
        self._tmp.cleanup()

    async def test_background_tools_are_registered(self):
        names = self.registry.all_names()

        self.assertIn("deep_research", names)
        self.assertIn("research_start", names)
        self.assertIn("research_status", names)
        self.assertIn("research_list", names)
        self.assertIn("research_cancel", names)
        self.assertIn("research_resume", names)

    async def test_research_start_and_status_via_executor(self):
        with patch("dotclaw.research.manager._search_sync") as search_mock, \
             patch("dotclaw.research.manager.fetch_url", side_effect=fake_fetch_url):
            search_mock.return_value = {
                "query": "工具问题",
                "results": [{"title": "A", "url": "https://example.com/a"}],
            }
            started = await self.executor.execute(
                "research_start",
                {"query": "工具问题", "depth": "quick"},
            )
            self.assertFalse(started.is_error)
            self.assertIn('"is_running": true', started.output)

            task_id = self.storage.list_tasks(1)[0].id
            for _ in range(50):
                status = await self.executor.execute("research_status", {"id": task_id})
                status_payload = json.loads(status.output)
                if status_payload["status"] == "done":
                    break
                await asyncio.sleep(0.01)

        self.assertFalse(status.is_error)
        self.assertTrue(status_payload["has_report"])

    async def test_research_list_via_executor(self):
        await self.manager.create_task("找回 ID", depth="quick")

        result = await self.executor.execute("research_list", {"limit": 5})

        self.assertFalse(result.is_error)
        self.assertIn("找回 ID", result.output)


if __name__ == "__main__":
    unittest.main()
