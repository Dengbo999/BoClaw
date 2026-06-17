"""工作区路径沙箱（安全模块收拢）。

收拢自 builtin/exec_tool.py 与 builtin/file_tool.py 中重复的
workspace 解析逻辑，以及 file_tool 的越界路径校验。
所有 builtin 工具统一经此判定可访问边界。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dotclaw.tools.base import ToolExecutionContext


def default_workspace() -> Path:
    """未显式传入上下文时，默认限制在 dotClaw 项目根目录。"""
    import dotclaw
    return Path(dotclaw.__file__).parent.parent.parent.resolve()


def workspace_from_context(context: "ToolExecutionContext | None") -> Path:
    """统一解析工具工作区，避免各工具自行决定访问边界。"""
    if context and context.workspace:
        return Path(context.workspace).expanduser().resolve()
    return default_workspace()


def resolve_workspace_path(
    path: str,
    context: "ToolExecutionContext | None",
) -> tuple[Path, Path]:
    """将用户路径解析到 workspace 内；越界路径直接拒绝。"""
    workspace = workspace_from_context(context)
    raw_path = Path(path).expanduser()
    candidate = raw_path if raw_path.is_absolute() else workspace / raw_path
    resolved = candidate.resolve(strict=False)

    try:
        resolved.relative_to(workspace)
    except ValueError as e:
        raise PermissionError(f"路径超出工作区: {path}") from e

    return resolved, workspace
