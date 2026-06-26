"""待办工具（个人助手）—— 闭包注入 TodoStore。

单工具多 action：add / list / done / remove，避免工具数量膨胀。
"""

from __future__ import annotations

from dotclaw.tools.handler import BuiltinToolHandler


def _fmt_item(item: dict) -> str:
    mark = "[x]" if item.get("done") else "[ ]"
    prio = item.get("priority", "normal")
    due = item.get("due")
    due_str = f" (截止 {due})" if due else ""
    return f"{mark} #{item['id']} {item['text']} [{prio}]{due_str}"


def get_todo_handler(store) -> BuiltinToolHandler:
    """构造 todo 工具 handler，闭包捕获 TodoStore 实例。"""

    async def todo(
        action: str,
        text: str | None = None,
        id: int | None = None,
        priority: str = "normal",
        filter: str = "active",
    ) -> str:
        try:
            if action == "add":
                if not text:
                    return "错误：add 需要提供 text"
                item = await store.add(text, priority=priority)
                return f"已添加 #{item['id']}: {item['text']} [{item['priority']}]"

            if action == "list":
                items = await store.list(filter=filter)
                if not items:
                    return "(没有待办事项)"
                header = {"active": "待办", "done": "已完成", "all": "全部"}.get(filter, "待办")
                lines = [f"{header}（{len(items)} 项）："]
                lines += [f"  {_fmt_item(i)}" for i in items]
                return "\n".join(lines)

            if action == "done":
                if id is None:
                    return "错误：done 需要提供 id"
                item = await store.set_done(int(id), done=True)
                return f"已完成 #{id}" if item else f"未找到待办 #{id}"

            if action == "remove":
                if id is None:
                    return "错误：remove 需要提供 id"
                ok = await store.remove(int(id))
                return f"已删除 #{id}" if ok else f"未找到待办 #{id}"

            return f"错误：未知 action '{action}'（支持 add/list/done/remove）"
        except Exception as e:
            return f"错误：{e}"

    return BuiltinToolHandler(
        name="todo",
        description=(
            "当用户要求添加待办、查看待办、完成待办、删除待办时，必须调用本工具，"
            "不能凭空说'已添加'而不调工具。"
            "action=add 新增（需 text，可选 priority=low/normal/high）；"
            "action=list 列出（filter=active/done/all，默认 active）；"
            "action=done 标记完成（需 id）；action=remove 删除（需 id）。"
            "注意：记录长期事实或用户偏好请用 memory_write，不要用本工具。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "list", "done", "remove"],
                    "description": "操作类型",
                },
                "text": {"type": "string", "description": "待办内容（add 必填）"},
                "id": {"type": "integer", "description": "待办编号（done/remove 必填）"},
                "priority": {
                    "type": "string",
                    "enum": ["low", "normal", "high"],
                    "description": "优先级（add 可选，默认 normal）",
                },
                "filter": {
                    "type": "string",
                    "enum": ["all", "active", "done"],
                    "description": "列表过滤（list 可选，默认 active）",
                },
            },
            "required": ["action"],
        },
        handler_fn=todo,
        needs_approval=False,
        timeout=10.0,
    )
