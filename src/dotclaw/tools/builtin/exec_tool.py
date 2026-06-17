"""Shell 执行工具（builtin 子包 — Phase 5 迁移 + W1 修复）

命令护栏（黑名单 / git 子命令 / 路径越界）与 workspace 解析已收拢至
dotclaw.tools.security，本模块只负责子进程执行与超时处理。
"""

from __future__ import annotations

import asyncio

from dotclaw.tools.base import ToolExecutionContext
from dotclaw.tools.handler import BuiltinToolHandler
from dotclaw.tools.security.path_sandbox import workspace_from_context
from dotclaw.tools.security.command_rules import (
    validate_sensitive_command,
    validate_command_workspace,
)


async def exec_command(
    command: str,
    context: ToolExecutionContext | None = None,
) -> str:
    """
    执行 Shell 命令，返回标准输出。

    Phase 5 W1 修复：添加 CancelledError 处理。
    当 ToolExecutor 的 asyncio.wait_for 超时 cancel task 时，
    CancelledError（Python 3.9+ 继承 BaseException，不被 except Exception 捕获）
    必须先 kill 子进程再重新抛出，避免孤儿进程。
    """
    proc = None
    try:
        workspace = workspace_from_context(context)
        validate_sensitive_command(command)
        validate_command_workspace(command, workspace)
        timeout = context.timeout if context and context.timeout else 60.0
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            output = stdout.decode("utf-8", errors="replace")
            return output if output else "(命令无输出)"
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return f"错误：命令执行超时（{int(timeout)}秒）"
        except asyncio.CancelledError:
            # Phase 5 W1 修复：ToolExecutor 超时 cancel → 必须 kill 子进程
            proc.kill()
            await proc.wait()
            raise  # 重新抛出，让 ToolExecutor 的 asyncio.wait_for 正常捕获
    except PermissionError as e:
        return f"错误：{e}"
    except asyncio.CancelledError:
        # proc 创建阶段被 cancel（极端情况）
        if proc is not None:
            proc.kill()
            await proc.wait()
        raise
    except Exception as e:
        return f"错误：{e}"


def get_exec_handler() -> BuiltinToolHandler:
    return BuiltinToolHandler(
        name="exec",
        description="执行一条 Shell 命令。危险操作，执行前需用户确认。",
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的命令（在工作区内执行，明显越界路径会被拒绝）",
                }
            },
            "required": ["command"],
        },
        handler_fn=exec_command,
        needs_approval=True,
        timeout=60.0,
    )
