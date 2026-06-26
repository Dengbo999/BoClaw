"""内置工具子包（Phase 5 新增）— 统一注册入口"""

from __future__ import annotations

from .exec_tool import get_exec_handler
from .file_tool import get_read_file_handler, get_write_file_handler, get_list_dir_handler
from .memory_tool import get_memory_read_handler, get_memory_write_handler
from .system_tool import get_system_info_handler, get_time_handler
from .web_fetch_tool import get_web_fetch_handler
from .web_search_tool import get_web_search_handler


def register_all(registry, include_web_search: bool = True):
    """
    注册所有内置工具到注册表。
    在 main.py 启动时调用。
    """
    handlers = [
        get_exec_handler(),
        get_read_file_handler(),
        get_write_file_handler(),
        get_list_dir_handler(),
        get_memory_read_handler(),
        get_memory_write_handler(),
        get_system_info_handler(),
        get_time_handler(),
    ]
    if include_web_search:
        handlers.append(get_web_search_handler())
        handlers.append(get_web_fetch_handler())
    for handler in handlers:
        registry.register(handler)


def register_assistant_tools(registry, todo_store=None, reminder_mgr=None):
    """注册个人助手工具（todo / reminder）。

    这些工具需要持有状态的 manager，通过闭包注入；故不在 register_all 中注册，
    而由 factory 在 manager 构建完成后单独调用。store 为 None 时跳过对应工具。
    """
    from .todo_tool import get_todo_handler
    from .reminder_tool import get_reminder_handler

    if todo_store is not None:
        registry.register(get_todo_handler(todo_store))
    if reminder_mgr is not None:
        registry.register(get_reminder_handler(reminder_mgr))


def register_research_tools(registry, research_mgr=None, research_runner=None):
    """注册深度研究工具。

    deep_research 需要持有 ResearchManager，ResearchManager 内部依赖 LLM 和研究存储，
    后台研究工具需要 ResearchRunner。二者都由 factory 完成构建后注入。
    """
    if research_mgr is None:
        return
    from .research_tool import (
        get_deep_research_handler,
        get_research_cancel_handler,
        get_research_continue_handler,
        get_research_list_handler,
        get_research_resume_handler,
        get_research_start_handler,
        get_research_status_handler,
    )

    registry.register(get_deep_research_handler(research_mgr))
    status_target = research_runner or research_mgr
    registry.register(get_research_status_handler(status_target))
    registry.register(get_research_resume_handler(status_target))
    registry.register(get_research_continue_handler(research_mgr))
    if research_runner is not None:
        registry.register(get_research_start_handler(research_runner))
        registry.register(get_research_list_handler(research_runner))
        registry.register(get_research_cancel_handler(research_runner))
