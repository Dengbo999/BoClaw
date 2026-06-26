"""Todo 数据层 —— 基于 JsonStore 的待办增删查改。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .store import JsonStore

_PRIORITIES = ("low", "normal", "high")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class TodoStore:
    """待办事项存储。每条：id/text/done/priority/created_at/done_at/due。"""

    def __init__(self, path):
        self._store = JsonStore(path)

    async def add(self, text: str, priority: str = "normal",
                  due: str | None = None) -> dict[str, Any]:
        """新增一条待办，返回该条记录。"""
        if priority not in _PRIORITIES:
            priority = "normal"

        def _fn(data):
            tid = JsonStore.take_next_id(data)
            item = {
                "id": tid,
                "text": text,
                "done": False,
                "priority": priority,
                "created_at": _now_iso(),
                "done_at": None,
                "due": due,
            }
            data["items"].append(item)
            return item

        return await self._store.mutate(_fn)

    async def list(self, filter: str = "active") -> list[dict[str, Any]]:
        """列出待办。filter: all / active / done。"""
        data = await self._store.read()
        items = data["items"]
        if filter == "active":
            items = [i for i in items if not i.get("done")]
        elif filter == "done":
            items = [i for i in items if i.get("done")]
        return items

    async def set_done(self, todo_id: int, done: bool = True) -> dict[str, Any] | None:
        """标记完成/未完成，返回更新后的记录；不存在返回 None。"""
        def _fn(data):
            for item in data["items"]:
                if item["id"] == todo_id:
                    item["done"] = done
                    item["done_at"] = _now_iso() if done else None
                    return item
            return None

        return await self._store.mutate(_fn)

    async def remove(self, todo_id: int) -> bool:
        """删除一条，返回是否删除成功。"""
        def _fn(data):
            before = len(data["items"])
            data["items"] = [i for i in data["items"] if i["id"] != todo_id]
            return len(data["items"]) < before

        return await self._store.mutate(_fn)
