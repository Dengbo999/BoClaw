"""个人助手数据层 —— JSON 持久化基类。

统一信封格式：{"version": 1, "next_id": N, "items": [...]}
特性：
- 原子写（写临时文件再 os.replace），避免半写损坏
- 每实例一把 asyncio.Lock，保证「读→改→写」串行
- 文件不存在 / 解析失败时返回空信封，不抛
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("dotclaw.assistant.store")

_VERSION = 1


def _empty_envelope() -> dict[str, Any]:
    return {"version": _VERSION, "next_id": 1, "items": []}


class JsonStore:
    """单文件 JSON 存储，带原子写和异步锁。"""

    def __init__(self, path: str | Path):
        self._path = Path(path).expanduser()
        self._lock = asyncio.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def _load_sync(self) -> dict[str, Any]:
        """同步读取（已在锁内调用）。损坏或不存在返回空信封。"""
        if not self._path.exists():
            return _empty_envelope()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("%s 解析失败，按空数据处理: %s", self._path.name, e)
            return _empty_envelope()
        # 容错：缺字段时补齐
        if not isinstance(data, dict):
            return _empty_envelope()
        data.setdefault("version", _VERSION)
        data.setdefault("next_id", 1)
        data.setdefault("items", [])
        if not isinstance(data["items"], list):
            data["items"] = []
        return data

    def _save_sync(self, data: dict[str, Any]) -> None:
        """同步原子写（已在锁内调用）。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self._path)

    async def read(self) -> dict[str, Any]:
        """读取整个信封。"""
        async with self._lock:
            return self._load_sync()

    async def mutate(self, fn):
        """在锁内执行「读→改→写」。

        fn(data) 接收当前信封 dict，可原地修改并返回任意值；
        修改后的 data 会被持久化，fn 的返回值原样返回给调用方。
        """
        async with self._lock:
            data = self._load_sync()
            result = fn(data)
            self._save_sync(data)
            return result

    @staticmethod
    def take_next_id(data: dict[str, Any]) -> int:
        """取出并自增 next_id（在 mutate 的 fn 内使用）。"""
        nid = int(data.get("next_id", 1))
        data["next_id"] = nid + 1
        return nid
