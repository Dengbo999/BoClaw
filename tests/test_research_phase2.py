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
from dotclaw.research.models import (
    ResearchNote,
    ResearchSource,
    ResearchStatus,
    ResearchTask,
    SourceStatus,
)
from dotclaw.tools.builtin import register_research_tools
from dotclaw.tools.executor import ToolExecutor
from dotclaw.tools.registry import ToolRegistry


class Phase2FakeLLM:
    async def chat(self, messages, tools=None, purpose="chat", stream=False, journal=None):
        system_text = messages[0].content
        if "证据提取助手" in system_text:
            yield ChatChunk(
                content=json.dumps([{
                    "claim": "该来源支持核心结论",
                    "evidence": "关键证据",
                    "relevance": "与问题直接相关",
                }], ensure_ascii=False),
                is_final=False,
            )
            yield ChatChunk(is_final=True, finish_reason="stop")
            return
        yield ChatChunk(content="## 结论\n综合 notes 得出结论 [1]。", is_final=False)
        yield ChatChunk(is_final=True, finish_reason="stop")


class SynthesizeFailingLLM(Phase2FakeLLM):
    async def chat(self, messages, tools=None, purpose="chat", stream=False, journal=None):
        system_text = messages[0].content
        if "证据提取助手" in system_text:
            async for chunk in super().chat(messages, tools, purpose, stream, journal):
                yield chunk
            return
        raise RuntimeError("synthesize boom")


async def fake_fetch_url(url: str, max_bytes: int = 200_000):
    return {
        "url": url,
        "title": "Fetched Title",
        "content_type": "text/html",
        "text": "这是网页正文，包含研究问题相关证据。",
        "truncated": False,
    }


class TestResearchPhase2(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.storage = ResearchStorage(Path(self._tmp.name) / "research")

    async def asyncTearDown(self):
        self._tmp.cleanup()

    async def test_run_writes_stage_state_and_notes(self):
        manager = ResearchManager(self.storage, llm=Phase2FakeLLM())
        with patch("dotclaw.research.manager._search_sync") as search_mock, \
             patch("dotclaw.research.manager.fetch_url", side_effect=fake_fetch_url):
            search_mock.return_value = {
                "query": "问题",
                "results": [{"title": "Result", "url": "https://example.com/a"}],
            }
            result = await manager.run("问题", depth="quick")

        self.assertEqual(result["status"], "done")
        self.assertEqual(result["note_count"], 1)
        task = self.storage.load_task(result["id"])
        self.assertEqual(task.status, ResearchStatus.DONE)
        for stage in ["planning", "searching", "reading", "extracting", "synthesizing"]:
            self.assertEqual(task.stages[stage].status.value, "done")
            self.assertIsNotNone(task.stages[stage].duration_ms)
        notes = self.storage.load_notes(result["id"])
        self.assertEqual(notes[0].claim, "该来源支持核心结论")

    async def test_status_reports_existing_task(self):
        manager = ResearchManager(self.storage, llm=None)
        task = ResearchTask.create("abc12345", "问题", "quick", manager._limits)
        self.storage.save_task(task.id, task)

        status = await manager.status("abc12345")
        self.assertEqual(status["id"], "abc12345")
        self.assertFalse(status["has_report"])

    async def test_resume_from_existing_sources(self):
        manager = ResearchManager(self.storage, llm=Phase2FakeLLM())
        task = ResearchTask.create("abc12345", "问题", "quick", manager._limits)
        task.status = ResearchStatus.FAILED
        self.storage.save_task(task.id, task)
        self.storage.save_sources(task.id, [
            ResearchSource(
                index=1,
                url="https://example.com/a",
                title="A",
                status=SourceStatus.FETCHED,
                excerpt="已有正文证据",
            )
        ])

        result = await manager.resume("abc12345")
        self.assertEqual(result["status"], "done")
        self.assertTrue(Path(result["report_path"]).exists())

    async def test_synthesize_llm_failure_writes_fallback_report(self):
        manager = ResearchManager(self.storage, llm=SynthesizeFailingLLM())
        with patch("dotclaw.research.manager._search_sync") as search_mock, \
             patch("dotclaw.research.manager.fetch_url", side_effect=fake_fetch_url):
            search_mock.return_value = {
                "query": "问题",
                "results": [{"title": "Result", "url": "https://example.com/a"}],
            }
            result = await manager.run("问题", depth="quick")

        self.assertEqual(result["status"], "done")
        self.assertIn("LLM 合成失败", result["report"])
        task = self.storage.load_task(result["id"])
        self.assertEqual(task.status, ResearchStatus.DONE)

    async def test_research_tools_registered_and_status_executes(self):
        manager = ResearchManager(self.storage, llm=None)
        task = ResearchTask.create("abc12345", "问题", "quick", manager._limits)
        self.storage.save_task(task.id, task)

        registry = ToolRegistry()
        register_research_tools(registry, manager)
        names = registry.all_names()
        self.assertIn("deep_research", names)
        self.assertIn("research_status", names)
        self.assertIn("research_resume", names)

        executor = ToolExecutor(registry)
        result = await executor.execute("research_status", {"id": "abc12345"})
        self.assertFalse(result.is_error)
        self.assertIn('"id": "abc12345"', result.output)


class TestResearchStoragePhase2(unittest.TestCase):
    def test_load_phase1_task_backfills_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = ResearchStorage(Path(tmp) / "research")
            task_dir = storage.task_dir("oldtask1")
            (task_dir / "task.json").write_text(json.dumps({
                "id": "oldtask1",
                "query": "旧问题",
                "depth": "quick",
                "status": "done",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }), encoding="utf-8")

            task = storage.load_task("oldtask1")
            self.assertEqual(task.id, "oldtask1")
            self.assertIn("planning", task.stages)
            self.assertEqual(storage.load_notes("oldtask1"), [])

    def test_research_id_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = ResearchStorage(Path(tmp) / "research")
            outside = Path(tmp) / "evil"

            with self.assertRaises(ValueError):
                storage.task_dir("../evil")
            with self.assertRaises(ValueError):
                storage.save_task("../evil", {"id": "../evil"})
            with self.assertRaises(ValueError):
                storage.load_task("../evil")

            self.assertFalse(outside.exists())


if __name__ == "__main__":
    unittest.main()
