"""TodoStore 数据层测试。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotclaw.assistant.todo import TodoStore


class TestTodoStore(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = TodoStore(Path(self._tmp.name) / "todos.json")

    async def asyncTearDown(self):
        self._tmp.cleanup()

    async def test_add_assigns_incrementing_ids(self):
        a = await self.store.add("买牛奶")
        b = await self.store.add("写代码", priority="high")
        self.assertEqual(a["id"], 1)
        self.assertEqual(b["id"], 2)
        self.assertEqual(b["priority"], "high")
        self.assertFalse(a["done"])

    async def test_invalid_priority_falls_back_to_normal(self):
        item = await self.store.add("x", priority="urgent")
        self.assertEqual(item["priority"], "normal")

    async def test_list_filters(self):
        await self.store.add("a")
        b = await self.store.add("b")
        await self.store.set_done(b["id"])

        active = await self.store.list(filter="active")
        done = await self.store.list(filter="done")
        all_ = await self.store.list(filter="all")
        self.assertEqual([i["text"] for i in active], ["a"])
        self.assertEqual([i["text"] for i in done], ["b"])
        self.assertEqual(len(all_), 2)

    async def test_set_done_and_undo(self):
        item = await self.store.add("task")
        done = await self.store.set_done(item["id"], done=True)
        self.assertTrue(done["done"])
        self.assertIsNotNone(done["done_at"])
        undone = await self.store.set_done(item["id"], done=False)
        self.assertFalse(undone["done"])
        self.assertIsNone(undone["done_at"])

    async def test_set_done_missing_returns_none(self):
        self.assertIsNone(await self.store.set_done(999))

    async def test_remove(self):
        item = await self.store.add("task")
        self.assertTrue(await self.store.remove(item["id"]))
        self.assertFalse(await self.store.remove(item["id"]))
        self.assertEqual(await self.store.list(filter="all"), [])

    async def test_persistence_across_instances(self):
        await self.store.add("persisted")
        store2 = TodoStore(self.store._store.path)
        items = await store2.list(filter="all")
        self.assertEqual([i["text"] for i in items], ["persisted"])

    async def test_corrupt_file_is_tolerated(self):
        self.store._store.path.parent.mkdir(parents=True, exist_ok=True)
        self.store._store.path.write_text("{ broken json", encoding="utf-8")
        # 损坏文件按空数据处理，add 仍可用
        item = await self.store.add("recover")
        self.assertEqual(item["id"], 1)


if __name__ == "__main__":
    unittest.main()
