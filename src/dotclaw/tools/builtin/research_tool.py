"""深度研究工具（个人助手/研究工作流）。"""

from __future__ import annotations

import json

from dotclaw.tools.handler import BuiltinToolHandler


def get_deep_research_handler(manager) -> BuiltinToolHandler:
    """构造 deep_research 工具 handler，闭包捕获 ResearchManager。"""

    async def deep_research(query: str, depth: str = "standard") -> str:
        try:
            result = await manager.run(query=query, depth=depth)
        except Exception as e:
            return f"错误：深度研究失败 - {e}"

        payload = {
            "id": result["id"],
            "status": result["status"],
            "query": result["query"],
            "source_count": result["source_count"],
            "note_count": result.get("note_count", 0),
            "report_path": result["report_path"],
            "report": result["report"],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    return BuiltinToolHandler(
        name="deep_research",
        description=(
            "同步执行深度研究：会等待搜索网页、读取来源、提取证据和生成 Markdown 报告全部完成后返回。"
            "报告会保存到 data/research/<id>/report.md。如需后台执行，请使用 research_start。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "研究问题",
                },
                "depth": {
                    "type": "string",
                    "enum": ["quick", "standard", "deep"],
                    "description": "研究深度。第一阶段会影响搜索 query 数量。",
                    "default": "standard",
                },
            },
            "required": ["query"],
        },
        handler_fn=deep_research,
        needs_approval=False,
        timeout=120.0,
    )


def get_research_start_handler(runner) -> BuiltinToolHandler:
    """构造 research_start 后台启动工具 handler。"""

    async def research_start(query: str, depth: str = "standard") -> str:
        try:
            result = await runner.start(query=query, depth=depth)
        except Exception as e:
            return f"错误：启动后台研究失败 - {e}"
        return json.dumps(result, ensure_ascii=False, indent=2)

    return BuiltinToolHandler(
        name="research_start",
        description=(
            "后台启动深度研究，立即返回研究任务 ID。适用于耗时较长的问题；"
            "后续用 research_status 查询进度，用 research_cancel 取消。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "研究问题",
                },
                "depth": {
                    "type": "string",
                    "enum": ["quick", "standard", "deep"],
                    "description": "研究深度",
                    "default": "standard",
                },
            },
            "required": ["query"],
        },
        handler_fn=research_start,
        needs_approval=False,
        timeout=10.0,
    )


def get_research_status_handler(manager_or_runner) -> BuiltinToolHandler:
    """构造 research_status 工具 handler。"""

    async def research_status(id: str) -> str:
        try:
            result = await manager_or_runner.status(id)
        except Exception as e:
            return f"错误：查询研究状态失败 - {e}"
        return json.dumps(result, ensure_ascii=False, indent=2)

    return BuiltinToolHandler(
        name="research_status",
        description="查询深度研究任务状态、阶段耗时、错误和报告是否已生成。",
        parameters={
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "研究任务 ID",
                },
            },
            "required": ["id"],
        },
        handler_fn=research_status,
        needs_approval=False,
        timeout=10.0,
    )


def get_research_list_handler(runner) -> BuiltinToolHandler:
    """构造 research_list 工具 handler。"""

    async def research_list(limit: int = 20) -> str:
        try:
            result = await runner.list(limit=limit)
        except Exception as e:
            return f"错误：列出研究任务失败 - {e}"
        return json.dumps(result, ensure_ascii=False, indent=2)

    return BuiltinToolHandler(
        name="research_list",
        description="列出最近的深度研究任务，便于找回 research_id 和报告路径。",
        parameters={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "返回任务数量，范围 1-100",
                    "default": 20,
                },
            },
        },
        handler_fn=research_list,
        needs_approval=False,
        timeout=10.0,
    )


def get_research_cancel_handler(runner) -> BuiltinToolHandler:
    """构造 research_cancel 工具 handler。"""

    async def research_cancel(id: str) -> str:
        try:
            result = await runner.cancel(id)
        except Exception as e:
            return f"错误：取消研究任务失败 - {e}"
        return json.dumps(result, ensure_ascii=False, indent=2)

    return BuiltinToolHandler(
        name="research_cancel",
        description="取消当前进程中正在后台运行的深度研究任务。",
        parameters={
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "研究任务 ID",
                },
            },
            "required": ["id"],
        },
        handler_fn=research_cancel,
        needs_approval=False,
        timeout=10.0,
    )


def get_research_resume_handler(manager_or_runner) -> BuiltinToolHandler:
    """构造 research_resume 工具 handler。"""

    async def research_resume(id: str, from_stage: str | None = None) -> str:
        try:
            result = await manager_or_runner.resume(id, from_stage=from_stage)
        except Exception as e:
            return f"错误：恢复研究失败 - {e}"
        return json.dumps(result, ensure_ascii=False, indent=2)

    return BuiltinToolHandler(
        name="research_resume",
        description=(
            "从已保存的研究任务恢复执行。可从 searching/reading/extracting/"
            "synthesizing 阶段继续。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "研究任务 ID",
                },
                "from_stage": {
                    "type": "string",
                    "enum": ["searching", "reading", "extracting", "synthesizing"],
                    "description": "可选，指定恢复阶段",
                },
            },
            "required": ["id"],
        },
        handler_fn=research_resume,
        needs_approval=False,
        timeout=120.0,
    )


def get_research_continue_handler(manager) -> BuiltinToolHandler:
    """构造 research_continue 同步继续研究工具 handler。"""

    async def research_continue(
        id: str,
        follow_up: str,
        depth: str = "standard",
    ) -> str:
        try:
            result = await manager.continue_research(
                research_id=id,
                follow_up=follow_up,
                depth=depth,
            )
        except Exception as e:
            return f"错误：继续研究失败 - {e}"
        return json.dumps(result, ensure_ascii=False, indent=2)

    return BuiltinToolHandler(
        name="research_continue",
        description=(
            "基于已有深度研究任务同步继续研究。会复用已有来源和证据，"
            "追加 follow-up 搜索结果并重新生成报告。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "研究任务 ID",
                },
                "follow_up": {
                    "type": "string",
                    "description": "继续研究的问题",
                },
                "depth": {
                    "type": "string",
                    "enum": ["quick", "standard", "deep"],
                    "description": "继续研究深度",
                    "default": "standard",
                },
            },
            "required": ["id", "follow_up"],
        },
        handler_fn=research_continue,
        needs_approval=False,
        timeout=120.0,
    )
