"""会话存储"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import aiofiles

SESSION_ID_PATTERN = re.compile(r"^[a-f0-9]{8}$")


def _resolve_data_dir(relative_path: str | Path) -> Path:
    """将相对路径解析为相对于项目根目录（config.yaml 所在目录）。"""
    # 向上找 config.yaml 来定位项目根目录
    import dotclaw
    module_path = Path(dotclaw.__file__).parent  # src/dotclaw/
    project_root = module_path.parent.parent  # 项目根目录
    resolved = project_root / relative_path
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


@dataclass
class SessionMessage:
    """会话中的一条消息"""
    role: str
    content: str
    name: str | None = None
    tool_call_id: str | None = None


@dataclass
class Session:
    """会话数据模型"""
    id: str
    title: str
    created_at: str
    updated_at: str
    messages: list[SessionMessage] = field(default_factory=list)
    model: str = "qwen-plus"
    summary: str | None = None
    summary_message_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


class SessionManager:
    """
    多会话管理。

    每个会话存储为独立的 JSON 文件。
    """

    def __init__(self, data_dir: str | Path):
        self._data_dir = _resolve_data_dir(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._data_dir = self._data_dir.resolve()

    @staticmethod
    def _dict_to_session(raw: dict) -> Session:
        """将 JSON 反序列化的 dict 转为 Session，确保 messages 是 SessionMessage 对象"""
        messages = []
        for m in raw.pop("messages", []):
            if isinstance(m, SessionMessage):
                messages.append(m)
            elif isinstance(m, dict):
                messages.append(SessionMessage(**m))
        raw["messages"] = messages
        return Session(**raw)

    @staticmethod
    def _validate_session_id(session_id: str) -> None:
        """会话 ID 只允许系统生成的 8 位十六进制，防止路径穿越。"""
        if not SESSION_ID_PATTERN.fullmatch(session_id):
            raise ValueError(f"非法会话 ID: {session_id}")

    def _session_path(self, session_id: str) -> Path:
        self._validate_session_id(session_id)
        path = (self._data_dir / f"{session_id}.json").resolve(strict=False)
        try:
            path.relative_to(self._data_dir)
        except ValueError as e:
            raise ValueError(f"会话路径超出目录: {session_id}") from e
        return path

    async def create(self, title: str = "新对话", model: str = "qwen-plus") -> Session:
        """创建新会话"""
        import uuid
        session = Session(
            id=str(uuid.uuid4())[:8],
            title=title,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
            model=model,
        )
        await self.save(session)
        return session

    async def load(self, session_id: str) -> Session | None:
        """加载会话"""
        try:
            path = self._session_path(session_id)
        except ValueError:
            return None
        if not path.exists():
            return None
        try:
            async with aiofiles.open(path, encoding="utf-8") as f:
                data = await f.read()
            return self._dict_to_session(json.loads(data))
        except Exception:
            return None

    async def save(self, session: Session) -> None:
        """保存会话"""
        self._validate_session_id(session.id)
        session.updated_at = datetime.now().isoformat()
        path = self._session_path(session.id)
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(session.to_dict(), ensure_ascii=False, indent=2))

    async def list_all(self) -> list[Session]:
        """列出所有会话（按更新时间倒序）"""
        sessions = []
        for path in self._data_dir.glob("*.json"):
            if not SESSION_ID_PATTERN.fullmatch(path.stem):
                continue
            try:
                async with aiofiles.open(path, encoding="utf-8") as f:
                    data = await f.read()
                sessions.append(self._dict_to_session(json.loads(data)))
            except Exception:
                pass
        sessions.sort(key=lambda s: s.updated_at, reverse=True)
        return sessions

    async def delete(self, session_id: str) -> bool:
        """删除会话"""
        try:
            path = self._session_path(session_id)
        except ValueError:
            return False
        if path.exists():
            path.unlink()
            return True
        return False
