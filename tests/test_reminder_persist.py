"""ReminderManager 持久化与重启恢复测试。"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotclaw.scheduler.reminder import ReminderManager


class CollectingChannel:
    """记录所有 send 内容的最小通道。"""

    def __init__(self):
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)


class TestReminderPersist(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "reminders.json"
        self.channel = CollectingChannel()

    async def asyncTearDown(self):
        self._tmp.cleanup()

    def _read(self) -> list[dict]:
        return json.loads(self.path.read_text(encoding="utf-8"))["items"]

    async def test_set_persists_pending(self):
        mgr = ReminderManager(self.path, channel=self.channel)
        rec = await mgr.set_reminder("开会", delay_seconds=100)
        items = self._read()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["status"], "pending")
        self.assertEqual(items[0]["id"], rec["id"])
        self.assertGreater(items[0]["fire_at"], datetime.now().timestamp())

    async def test_fires_and_marks_fired(self):
        mgr = ReminderManager(self.path, channel=self.channel)
        await mgr.set_reminder("喝水", delay_seconds=0.05)
        await asyncio.sleep(0.2)
        self.assertTrue(any("喝水" in m for m in self.channel.sent))
        self.assertEqual(self._read()[0]["status"], "fired")

    async def test_cancel(self):
        mgr = ReminderManager(self.path, channel=self.channel)
        rec = await mgr.set_reminder("作废", delay_seconds=100)
        self.assertTrue(await mgr.cancel_reminder(rec["id"]))
        self.assertEqual(self._read()[0]["status"], "cancelled")
        # 取消后不应再触发
        await asyncio.sleep(0.1)
        self.assertFalse(self.channel.sent)

    async def test_restore_reschedules_future(self):
        # 手写一条很快到期的未来提醒（模拟重启前落盘）
        fire_at = datetime.now().timestamp() + 0.1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "version": 1, "next_id": 1,
            "items": [{
                "id": "ab12cd34", "message": "续命", "fire_at": fire_at,
                "status": "pending", "created_at": "2026-06-17T00:00:00",
            }],
        }), encoding="utf-8")

        mgr = ReminderManager(self.path, channel=self.channel)
        stats = await mgr.restore()
        self.assertEqual(stats["rescheduled"], 1)
        await asyncio.sleep(0.25)
        self.assertTrue(any("续命" in m for m in self.channel.sent))
        self.assertEqual(self._read()[0]["status"], "fired")

    async def test_restore_resends_missed(self):
        # 已过期的 pending：恢复时应立即补发 + 标 missed
        fire_at = datetime.now().timestamp() - 60
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "version": 1, "next_id": 1,
            "items": [{
                "id": "deadbeef", "message": "错过了", "fire_at": fire_at,
                "status": "pending", "created_at": "2026-06-17T00:00:00",
            }],
        }), encoding="utf-8")

        mgr = ReminderManager(self.path, channel=self.channel)
        stats = await mgr.restore()
        self.assertEqual(stats["missed"], 1)
        self.assertTrue(any("错过了" in m and "错过" in m for m in self.channel.sent))
        self.assertEqual(self._read()[0]["status"], "missed")

    async def test_restore_missed_without_channel_only_marks(self):
        fire_at = datetime.now().timestamp() - 60
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "version": 1, "next_id": 1,
            "items": [{
                "id": "nochannel", "message": "静默", "fire_at": fire_at,
                "status": "pending", "created_at": "2026-06-17T00:00:00",
            }],
        }), encoding="utf-8")

        mgr = ReminderManager(self.path, channel=None)
        stats = await mgr.restore()
        self.assertEqual(stats["missed"], 1)
        self.assertEqual(self._read()[0]["status"], "missed")

    async def test_set_with_absolute_at(self):
        mgr = ReminderManager(self.path, channel=self.channel)
        at = datetime(2030, 1, 1, 9, 0, 0).isoformat()
        rec = await mgr.set_reminder("未来", at=at)
        self.assertAlmostEqual(
            rec["fire_at"], datetime(2030, 1, 1, 9, 0, 0).timestamp(), places=0
        )
        await mgr.cancel_reminder(rec["id"])  # 清理任务


if __name__ == "__main__":
    unittest.main()
