"""工具安全模块（收拢 workspace 限制、命令护栏、审批与审计）。

兑现 executor 中的历史 TODO：把分散在 approval / exec_tool / file_tool
的安全逻辑统一收拢到此子包。
"""

from __future__ import annotations

from .path_sandbox import (
    default_workspace,
    workspace_from_context,
    resolve_workspace_path,
)
from .command_rules import (
    validate_sensitive_command,
    validate_command_workspace,
)
from .approval import ApprovalManager

__all__ = [
    "default_workspace",
    "workspace_from_context",
    "resolve_workspace_path",
    "validate_sensitive_command",
    "validate_command_workspace",
    "ApprovalManager",
]
