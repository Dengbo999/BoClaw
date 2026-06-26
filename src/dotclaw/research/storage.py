"""深度研究结果持久化。"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RESEARCH_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class ResearchStorage:
    """按 research_id 保存研究任务、来源和报告。"""

    def __init__(self, base_dir: str | Path):
        self._base_dir = Path(base_dir).resolve(strict=False)
        self._base_dir.mkdir(parents=True, exist_ok=True)

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    @staticmethod
    def _validate_research_id(research_id: str) -> None:
        """研究 ID 只允许简单目录名，避免外部工具参数造成路径穿越。"""
        if not RESEARCH_ID_PATTERN.fullmatch(research_id or ""):
            raise ValueError(f"非法研究任务 ID: {research_id}")

    def _task_dir_path(self, research_id: str) -> Path:
        self._validate_research_id(research_id)
        path = (self._base_dir / research_id).resolve(strict=False)
        try:
            path.relative_to(self._base_dir)
        except ValueError as e:
            raise ValueError(f"研究任务路径超出目录: {research_id}") from e
        return path

    def task_dir(self, research_id: str) -> Path:
        path = self._task_dir_path(research_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _existing_task_dir(self, research_id: str) -> Path | None:
        path = self._task_dir_path(research_id)
        if not path.exists() or not path.is_dir():
            return None
        return path

    @staticmethod
    def _to_plain(value: Any) -> Any:
        if hasattr(value, "to_dict"):
            return value.to_dict()
        if isinstance(value, list):
            return [ResearchStorage._to_plain(item) for item in value]
        return value

    @staticmethod
    def _read_json(path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    def save_task(self, research_id: str, task: Any) -> Path:
        path = self.task_dir(research_id) / "task.json"
        path.write_text(
            json.dumps(self._to_plain(task), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def load_task(self, research_id: str):
        task_dir = self._existing_task_dir(research_id)
        if not task_dir:
            return None
        raw = self._read_json(task_dir / "task.json", None)
        if raw is None:
            return None
        from .models import ResearchTask
        return ResearchTask.from_dict(raw)

    def save_sources(self, research_id: str, sources: list[Any]) -> Path:
        path = self.task_dir(research_id) / "sources.json"
        path.write_text(
            json.dumps(self._to_plain(sources), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def load_sources(self, research_id: str):
        task_dir = self._existing_task_dir(research_id)
        if not task_dir:
            return []
        raw = self._read_json(task_dir / "sources.json", [])
        from .models import ResearchSource
        return [ResearchSource.from_dict(item) for item in raw if isinstance(item, dict)]

    def save_notes(self, research_id: str, notes: list[Any]) -> Path:
        path = self.task_dir(research_id) / "notes.json"
        path.write_text(
            json.dumps(self._to_plain(notes), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def load_notes(self, research_id: str):
        task_dir = self._existing_task_dir(research_id)
        if not task_dir:
            return []
        raw = self._read_json(task_dir / "notes.json", [])
        from .models import ResearchNote
        return [ResearchNote.from_dict(item) for item in raw if isinstance(item, dict)]

    def save_report(self, research_id: str, report: str) -> Path:
        path = self.task_dir(research_id) / "report.md"
        path.write_text(report, encoding="utf-8")
        return path

    def load_report(self, research_id: str) -> str | None:
        task_dir = self._existing_task_dir(research_id)
        if not task_dir:
            return None
        path = task_dir / "report.md"
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def list_tasks(self, limit: int = 20):
        from .models import ResearchTask

        tasks: list[ResearchTask] = []
        for path in sorted(self._base_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not path.is_dir():
                continue
            raw = self._read_json(path / "task.json", None)
            if raw is None:
                continue
            tasks.append(ResearchTask.from_dict(raw))
            if len(tasks) >= limit:
                break
        return tasks
