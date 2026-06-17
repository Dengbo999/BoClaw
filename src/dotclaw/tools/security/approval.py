"""工具审批管理器（安全模块收拢 — 原 tools/approval.py）"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dotclaw.channel.base import Channel


_SENSITIVE_KEYWORDS = ("api_key", "apikey", "token", "password", "secret", "authorization")


def _default_audit_log_path() -> Path:
    """默认写入项目运行数据目录，避免把审批记录混进源码。"""
    import dotclaw
    project_root = Path(dotclaw.__file__).parent.parent.parent
    return project_root / "data" / "security" / "approvals.jsonl"


def _redact(value: Any) -> Any:
    """递归脱敏审批参数，防止日志记录密钥类字段。"""
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if any(word in key_text for word in _SENSITIVE_KEYWORDS):
                redacted[key] = "***REDACTED***"
            else:
                redacted[key] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


class ApprovalManager:
    """
    危险工具执行前需要用户确认。

    审批策略（双重）：
    1. ToolDefinition.needs_approval 声明式（工具自己声明）
    2. config.tools.approval_commands 列表（用户配置覆盖）

    Phase 5 关键变化：
    - 删除硬编码 NEEDS_APPROVAL = {"exec", "python"}
    - 新增 _approval_commands 集合，从 config.yaml 加载
    """

    def __init__(
        self,
        approval_commands: list[str] | None = None,
        audit_log_path: str | Path | None = None,
    ):
        self._enabled = True
        self._approval_commands = set(approval_commands or [])
        self._audit_log_path = (
            Path(audit_log_path) if audit_log_path is not None else _default_audit_log_path()
        )

    def set_enabled(self, enabled: bool):
        self._enabled = enabled

    def set_approval_commands(self, commands: list[str]):
        """从 config.yaml 加载需要审批的命令列表"""
        self._approval_commands = set(commands)

    async def check(
        self,
        tool_name: str,
        arguments: dict,
        channel: "Channel | None" = None,
    ) -> bool:
        """
        检查工具是否需要审批。

        逻辑：
        1. _enabled=False -> 全部放行
        2. tool_name 在 _approval_commands 中 -> 需要审批
        3. 否则放行
        """
        if not self._enabled:
            self._write_audit(tool_name, arguments, decision="auto_approved_disabled")
            return True

        if tool_name not in self._approval_commands:
            self._write_audit(tool_name, arguments, decision="auto_approved_not_configured")
            return True

        if channel is None:
            # 无 channel 时默认放行（子 Agent 场景）
            self._write_audit(tool_name, arguments, decision="auto_approved_no_channel")
            return True

        # 通过 channel 向用户请求确认
        args_str = json.dumps(arguments, ensure_ascii=False, indent=2)
        confirm = await channel.ask_user(
            f"⚠️ 即将执行危险工具 `{tool_name}`\n"
            f"参数：{args_str}\n"
            f"确认执行？(y/n): "
        )
        approved = confirm.strip().lower() in ("y", "yes")
        self._write_audit(
            tool_name,
            arguments,
            decision="approved" if approved else "denied",
        )
        return approved

    def _write_audit(self, tool_name: str, arguments: dict, decision: str) -> None:
        """记录危险工具审批结果；日志失败不能阻塞正常工具流程。"""
        try:
            self._audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            event = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "tool_name": tool_name,
                "decision": decision,
                "arguments": _redact(arguments),
            }
            with open(self._audit_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except Exception:
            pass
