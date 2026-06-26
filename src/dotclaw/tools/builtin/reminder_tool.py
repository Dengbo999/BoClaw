"""提醒工具（个人助手）—— 闭包注入 ReminderManager。

单工具多 action：set / list / cancel。
相对时间（'明天9点'）由 LLM 先调 get_time 换算成绝对时间传 at。
"""

from __future__ import annotations

from datetime import datetime

from dotclaw.tools.handler import BuiltinToolHandler


def _fmt(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def get_reminder_handler(manager) -> BuiltinToolHandler:
    """构造 reminder 工具 handler，闭包捕获 ReminderManager 实例。"""

    async def reminder(
        action: str,
        message: str | None = None,
        at: str | None = None,
        delay_seconds: float | None = None,
        id: str | None = None,
    ) -> str:
        try:
            if action == "set":
                if not message:
                    return "错误：set 需要提供 message"
                if at is None and delay_seconds is None:
                    return "错误：set 需要提供 at（绝对时间）或 delay_seconds（相对秒数）"
                record = await manager.set_reminder(
                    message, delay_seconds=delay_seconds, at=at
                )
                return (
                    f"已设提醒 #{record['id']}，将在 "
                    f"{_fmt(record['fire_at'])} 触发：{record['message']}"
                )

            if action == "list":
                items = await manager.list_reminders()
                if not items:
                    return "(没有待触发的提醒)"
                lines = [f"待触发提醒（{len(items)} 条）："]
                lines += [
                    f"  #{i['id']} {_fmt(i['fire_at'])} — {i['message']}"
                    for i in items
                ]
                return "\n".join(lines)

            if action == "cancel":
                if not id:
                    return "错误：cancel 需要提供 id"
                ok = await manager.cancel_reminder(id)
                return f"已取消提醒 #{id}" if ok else f"未找到待触发的提醒 #{id}"

            return f"错误：未知 action '{action}'（支持 set/list/cancel）"
        except Exception as e:
            return f"错误：{e}"

    return BuiltinToolHandler(
        name="reminder",
        description=(
            "当用户要求设置提醒、取消提醒、查看提醒时，必须调用本工具，"
            "不能凭空说'已设置'而不调工具。"
            "到点后系统会主动推送消息给用户。"
            "action=set 设置（需 message；时间用 at 绝对 ISO 格式如 "
            "2026-06-18T09:00:00，或 delay_seconds 相对秒数）；"
            "action=list 列出待触发提醒；action=cancel 取消（需 id）。"
            "若用户用相对时间表述（如'明天9点'、'下午3点'、'30分钟后'），"
            "必须先调用 get_time 获取当前时间，换算为绝对时间后，"
            "立即调用本工具 action=set 并传入 at。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["set", "list", "cancel"],
                    "description": "操作类型",
                },
                "message": {"type": "string", "description": "提醒内容（set 必填）"},
                "at": {
                    "type": "string",
                    "description": "绝对触发时间，ISO 格式（set 时与 delay_seconds 二选一）",
                },
                "delay_seconds": {
                    "type": "number",
                    "description": "相对延迟秒数（set 时与 at 二选一）",
                },
                "id": {"type": "string", "description": "提醒编号（cancel 必填）"},
            },
            "required": ["action"],
        },
        handler_fn=reminder,
        needs_approval=False,
        timeout=10.0,
    )
