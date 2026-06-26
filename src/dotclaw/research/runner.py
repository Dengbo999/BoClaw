"""深度研究后台任务 runner。"""

from __future__ import annotations

import asyncio
from typing import Any

from .manager import ResearchManager
from .models import ResearchStatus, ResearchTask


class ResearchRunner:
    """管理当前进程内运行的深度研究后台任务。"""

    def __init__(self, manager: ResearchManager, max_concurrency: int = 2):
        self._manager = manager
        self._max_concurrency = max(1, int(max_concurrency))
        self._tasks: dict[str, asyncio.Task] = {}

    async def start(self, query: str, depth: str = "standard") -> dict[str, Any]:
        """创建研究任务并在后台启动，立即返回任务 ID。"""
        self._prune_done_tasks()
        if len(self._tasks) >= self._max_concurrency:
            raise RuntimeError(f"后台研究任务已达到并发上限: {self._max_concurrency}")

        task = await self._manager.create_task(query=query, depth=depth)
        handle = asyncio.create_task(self._run_background(task.id))
        self._tasks[task.id] = handle
        return {
            "id": task.id,
            "status": task.status.value,
            "query": task.query,
            "depth": task.depth,
            "is_running": True,
            "message": "研究任务已在后台启动，可用 research_status 查询进度。",
        }

    async def status(self, research_id: str) -> dict[str, Any]:
        """查询研究任务状态，并追加当前进程内的运行态。"""
        data = await self._manager.status(research_id)
        is_running = self.is_running(research_id)
        data["is_running"] = is_running
        data["can_cancel"] = is_running
        data["can_resume"] = self._can_resume(data, is_running)
        return data

    async def list(self, limit: int = 20) -> list[dict[str, Any]]:
        """列出最近研究任务摘要。"""
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 20
        limit = max(1, min(limit, 100))

        self._prune_done_tasks()
        return [self._task_summary(task) for task in self._manager.storage.list_tasks(limit)]

    async def cancel(self, research_id: str) -> dict[str, Any]:
        """取消当前进程内仍在运行的研究任务。"""
        handle = self._tasks.get(research_id)
        if handle is None or handle.done():
            self._tasks.pop(research_id, None)
            data = await self._manager.status(research_id)
            data["cancelled"] = False
            data["message"] = "研究任务当前未在后台运行，无法取消。"
            data["is_running"] = False
            data["can_cancel"] = False
            data["can_resume"] = self._can_resume(data, False)
            return data

        handle.cancel()
        try:
            await handle
        except asyncio.CancelledError:
            pass
        self._tasks.pop(research_id, None)
        data = await self._manager.status(research_id)
        data["cancelled"] = data.get("status") == ResearchStatus.CANCELLED.value
        data["is_running"] = False
        data["can_cancel"] = False
        data["can_resume"] = self._can_resume(data, False)
        return data

    async def resume(
        self,
        research_id: str,
        from_stage: str | None = None,
    ) -> dict[str, Any]:
        """恢复非运行中的研究任务。"""
        if self.is_running(research_id):
            raise RuntimeError(f"研究任务正在运行，不能重复恢复: {research_id}")
        result = await self._manager.resume(research_id, from_stage=from_stage)
        result["is_running"] = False
        result["can_cancel"] = False
        result["can_resume"] = False
        return result

    async def shutdown(self) -> None:
        """取消当前进程内所有仍在运行的后台研究任务。"""
        for research_id in list(self._tasks):
            await self.cancel(research_id)

    def is_running(self, research_id: str) -> bool:
        """判断任务是否仍在当前进程后台运行。"""
        handle = self._tasks.get(research_id)
        if handle is None:
            return False
        if handle.done():
            self._tasks.pop(research_id, None)
            return False
        return True

    async def _run_background(self, research_id: str) -> None:
        try:
            await self._manager.run_task(research_id)
        except asyncio.CancelledError:
            await self._manager.mark_cancelled(research_id, "用户取消研究任务")
            raise
        except Exception as e:
            await self._manager.mark_failed(research_id, e)
        finally:
            current = asyncio.current_task()
            if self._tasks.get(research_id) is current:
                self._tasks.pop(research_id, None)

    def _prune_done_tasks(self) -> None:
        for research_id, handle in list(self._tasks.items()):
            if handle.done():
                self._tasks.pop(research_id, None)

    def _task_summary(self, task: ResearchTask) -> dict[str, Any]:
        is_running = self.is_running(task.id)
        return {
            "id": task.id,
            "query": task.query,
            "depth": task.depth,
            "status": task.status.value,
            "is_running": is_running,
            "can_cancel": is_running,
            "can_resume": self._can_resume(task.to_dict(), is_running),
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "source_count": task.source_count,
            "note_count": task.note_count,
            "report_path": task.report_path,
            "error": task.error,
            "progress": task.progress,
        }

    @staticmethod
    def _can_resume(data: dict[str, Any], is_running: bool) -> bool:
        if is_running:
            return False
        return data.get("status") in {
            ResearchStatus.FAILED.value,
            ResearchStatus.CANCELLED.value,
            ResearchStatus.READING.value,
            ResearchStatus.EXTRACTING.value,
            ResearchStatus.SYNTHESIZING.value,
        }
