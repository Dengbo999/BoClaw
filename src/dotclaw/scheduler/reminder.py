"""定时提醒模块 —— 内存 asyncio 任务 + JSON 持久化 + 重启恢复。

提醒以绝对时间 fire_at（epoch 秒）持久化，进程重启后可恢复：
- fire_at 仍在未来 → 重新挂起任务
- fire_at 已过去（错过）→ 立即补发一次并标记 missed
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from dotclaw.assistant.store import JsonStore

if TYPE_CHECKING:
    from ..channel.base import Channel

logger = logging.getLogger("dotclaw.scheduler.reminder")


def _now_ts() -> float:
    return datetime.now().timestamp()


def _fmt(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


class ReminderManager:
    """一次性提醒：支持相对延迟 / 绝对时间，持久化到 JSON。"""

    def __init__(self, store_path, channel: "Channel | None" = None):
        self._tasks: dict[str, asyncio.Task] = {}
        self._channel = channel
        self._store = JsonStore(store_path)

    def set_channel(self, channel: "Channel"):
        self._channel = channel

    # ── 设置 ──────────────────────────────────────────────

    async def set_reminder(
        self,
        message: str,
        delay_seconds: float | None = None,
        at: str | None = None,
    ) -> dict[str, Any]:
        """设置一次性提醒。

        delay_seconds 与 at 二选一（at 为 ISO 时间字符串，优先）。
        返回提醒记录 dict（含 id / fire_at）。
        """
        if at:
            fire_at = datetime.fromisoformat(at).timestamp()
        elif delay_seconds is not None:
            fire_at = _now_ts() + float(delay_seconds)
        else:
            raise ValueError("必须提供 delay_seconds 或 at")

        reminder_id = str(uuid.uuid4())[:8]
        record = {
            "id": reminder_id,
            "message": message,
            "fire_at": fire_at,
            "status": "pending",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }

        def _fn(data):
            data["items"].append(record)
            return record

        await self._store.mutate(_fn)
        self._schedule_task(reminder_id, message, fire_at)
        return record

    # ── 取消 / 列出 ───────────────────────────────────────

    async def cancel_reminder(self, reminder_id: str) -> bool:
        """取消一个提醒（取消内存任务 + 标记 cancelled）。"""
        task = self._tasks.pop(reminder_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        def _fn(data):
            for item in data["items"]:
                if item["id"] == reminder_id and item["status"] == "pending":
                    item["status"] = "cancelled"
                    return True
            return False

        return await self._store.mutate(_fn)

    async def list_reminders(self, include_done: bool = False) -> list[dict[str, Any]]:
        """列出提醒。默认只列 pending。"""
        data = await self._store.read()
        items = data["items"]
        if not include_done:
            items = [i for i in items if i.get("status") == "pending"]
        return sorted(items, key=lambda i: i.get("fire_at", 0))

    # ── 重启恢复 ──────────────────────────────────────────

    async def restore(self) -> dict[str, int]:
        """启动时恢复未触发的提醒。返回 {rescheduled, missed} 统计。"""
        now = _now_ts()
        rescheduled = 0
        missed_ids: list[tuple[str, str]] = []

        data = await self._store.read()
        for item in data["items"]:
            if item.get("status") != "pending":
                continue
            fire_at = float(item.get("fire_at", 0))
            if fire_at > now:
                self._schedule_task(item["id"], item["message"], fire_at)
                rescheduled += 1
            else:
                missed_ids.append((item["id"], item["message"]))

        # 错过的：立即补发（有 channel 时）+ 标 missed
        for rid, msg in missed_ids:
            if self._channel:
                try:
                    await self._channel.send(f"⏰ (错过的提醒) {msg}")
                except Exception as e:
                    logger.warning("补发错过提醒失败: %s", e)
            await self._mark_status(rid, "missed")

        if rescheduled or missed_ids:
            logger.info("提醒恢复：重新挂起 %d 条，错过 %d 条", rescheduled, len(missed_ids))
        return {"rescheduled": rescheduled, "missed": len(missed_ids)}

    # ── 内部 ──────────────────────────────────────────────

    def _schedule_task(self, reminder_id: str, message: str, fire_at: float) -> None:
        """挂起一个到点推送的 asyncio 任务。"""
        async def _remind():
            try:
                delay = max(0.0, fire_at - _now_ts())
                await asyncio.sleep(delay)
                if self._channel:
                    await self._channel.send(f"⏰ 提醒: {message}")
                await self._mark_status(reminder_id, "fired")
            except asyncio.CancelledError:
                pass  # 被取消，正常退出
            finally:
                self._tasks.pop(reminder_id, None)

        self._tasks[reminder_id] = asyncio.create_task(_remind())

    async def _mark_status(self, reminder_id: str, status: str) -> None:
        def _fn(data):
            for item in data["items"]:
                if item["id"] == reminder_id:
                    item["status"] = status
                    return True
            return False

        await self._store.mutate(_fn)
