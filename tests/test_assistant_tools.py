"""个人助手工具端到端测试（经 ToolRegistry / ToolExecutor）。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotclaw.assistant.todo import TodoStore
from dotclaw.scheduler.reminder import ReminderManager
from dotclaw.tools.builtin import register_assistant_tools
from dotclaw.tools.registry import ToolRegistry
from dotclaw.tools.executor import ToolExecutor


class TestAssistantTools(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.todo_store = TodoStore(base / "todos.json")
        self.reminder_mgr = ReminderManager(base / "reminders.json")
        self.registry = ToolRegistry()
        register_assistant_tools(self.registry, self.todo_store, self.reminder_mgr)
        self.executor = ToolExecutor(registry=self.registry)

    async def asyncTearDown(self):
        self._tmp.cleanup()

    async def test_both_tools_registered(self):
        names = self.registry.all_names()
        self.assertIn("todo", names)
        self.assertIn("reminder", names)

    async def test_todo_add_then_list_via_executor(self):
        r = await self.executor.execute("todo", {"action": "add", "text": "买菜"})
        self.assertFalse(r.is_error)
        self.assertIn("已添加 #1", r.output)
        # 闭包注入的 store 真的被写入
        items = await self.todo_store.list(filter="all")
        self.assertEqual(items[0]["text"], "买菜")

        r2 = await self.executor.execute("todo", {"action": "list"})
        self.assertIn("买菜", r2.output)

    async def test_todo_done_and_remove(self):
        await self.executor.execute("todo", {"action": "add", "text": "t"})
        r = await self.executor.execute("todo", {"action": "done", "id": 1})
        self.assertIn("已完成 #1", r.output)
        r2 = await self.executor.execute("todo", {"action": "remove", "id": 1})
        self.assertIn("已删除 #1", r2.output)

    async def test_todo_missing_text_is_graceful(self):
        r = await self.executor.execute("todo", {"action": "add"})
        self.assertIn("需要提供 text", r.output)

    async def test_reminder_set_and_list_via_executor(self):
        r = await self.executor.execute(
            "reminder", {"action": "set", "message": "开会", "delay_seconds": 100}
        )
        self.assertFalse(r.is_error)
        self.assertIn("已设提醒", r.output)
        r2 = await self.executor.execute("reminder", {"action": "list"})
        self.assertIn("开会", r2.output)

    async def test_context_not_misinjected(self):
        """闭包 handler 不声明 context 形参，executor 传入 workspace 不应报错。"""
        r = await self.executor.execute(
            "todo", {"action": "add", "text": "ws"}, workspace=Path(self._tmp.name)
        )
        self.assertFalse(r.is_error)


if __name__ == "__main__":
    unittest.main()
