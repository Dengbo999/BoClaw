"""Shell 执行工具（builtin 子包 — Phase 5 迁移 + W1 修复）"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from dotclaw.tools.base import ToolExecutionContext
from dotclaw.tools.handler import BuiltinToolHandler

_SHELL_SEPARATORS = {";", "&&", "||", "|", "&"}

_BLOCKED_COMMANDS = {
    "rm",
    "rm.exe",
    "del",
    "del.exe",
    "erase",
    "erase.exe",
    "rmdir",
    "rmdir.exe",
    "rd",
    "remove-item",
    "ri",
    "shutdown",
    "shutdown.exe",
    "restart-computer",
    "stop-computer",
    "format",
    "format.com",
    "diskpart",
    "diskpart.exe",
    "reg",
    "reg.exe",
    "regedit",
    "regedit.exe",
    "sc",
    "sc.exe",
    "net",
    "net.exe",
    "net1",
    "net1.exe",
    "bcdedit",
    "bcdedit.exe",
    "takeown",
    "takeown.exe",
    "icacls",
    "icacls.exe",
    "chmod",
    "chown",
    "chgrp",
    "taskkill",
    "taskkill.exe",
    "kill",
    "pkill",
    "sudo",
    "su",
    "runas",
    "runas.exe",
    "set-executionpolicy",
}

_DANGEROUS_GIT_SUBCOMMANDS = {"reset", "clean"}


def _default_workspace() -> Path:
    """未传入上下文时，默认把命令限制在项目根目录内执行。"""
    import dotclaw
    return Path(dotclaw.__file__).parent.parent.parent.resolve()


def _workspace_from_context(context: ToolExecutionContext | None) -> Path:
    """统一命令工作目录，避免不同调用路径出现不同根目录。"""
    if context and context.workspace:
        return Path(context.workspace).expanduser().resolve()
    return _default_workspace()


def _tokenize_command(command: str) -> list[str]:
    """尽量把 shell 命令切成可检查的参数片段。"""
    pattern = re.compile(r'''(?:"([^"]+)"|'([^']+)'|(\S+))''')
    tokens: list[str] = []
    for match in pattern.finditer(command):
        token = next((g for g in match.groups() if g is not None), "")
        if token:
            tokens.append(token)
    return tokens


def _command_name(token: str) -> str:
    """归一化命令名，便于匹配 path/to/cmd.exe 这类写法。"""
    stripped = token.strip().lower()
    if not stripped:
        return ""
    return Path(stripped).name


def _find_blocked_command_text(text: str) -> str | None:
    """在 shell wrapper 的文本参数里查找危险命令名。"""
    lowered = text.lower()
    for blocked in sorted(_BLOCKED_COMMANDS, key=len, reverse=True):
        base = blocked.removesuffix(".exe").removesuffix(".com")
        if re.search(rf"(?<![\w-]){re.escape(base)}(?![\w-])", lowered):
            return base
    return None


def _command_positions(tokens: list[str]) -> list[int]:
    """找出每个 shell 片段的命令位置，用于敏感命令检查。"""
    positions: list[int] = []
    expect_command = True
    for index, token in enumerate(tokens):
        if token in _SHELL_SEPARATORS:
            expect_command = True
            continue
        if token.startswith((">", "<")):
            continue
        if expect_command:
            positions.append(index)
            expect_command = False
    return positions


def _validate_sensitive_command(command: str) -> None:
    """拒绝高风险命令；这些操作不应由裸 shell 工具承担。"""
    tokens = _tokenize_command(command)
    positions = _command_positions(tokens)

    for pos in positions:
        raw = tokens[pos]
        cmd = _command_name(raw)
        if cmd in _BLOCKED_COMMANDS:
            raise PermissionError(f"敏感命令被拒绝: {raw}")

        if cmd in ("cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe"):
            blocked = _find_blocked_command_text(" ".join(tokens[pos + 1:]))
            if blocked:
                raise PermissionError(f"敏感 shell 命令被拒绝: {blocked}")

        if cmd in ("git", "git.exe"):
            _validate_git_command(tokens, pos)


def _validate_git_command(tokens: list[str], git_pos: int) -> None:
    """拦截会破坏工作区状态或历史的 git 子命令。"""
    args = [t.lower() for t in tokens[git_pos + 1:] if t not in _SHELL_SEPARATORS]
    if not args:
        return

    subcommand = args[0]
    if subcommand in _DANGEROUS_GIT_SUBCOMMANDS:
        raise PermissionError(f"敏感 git 命令被拒绝: git {subcommand}")

    if subcommand == "checkout" and "--" in args:
        raise PermissionError("敏感 git 命令被拒绝: git checkout --")

    if subcommand == "restore":
        raise PermissionError("敏感 git 命令被拒绝: git restore")

    if subcommand == "push" and any(a.startswith("--force") for a in args):
        raise PermissionError("敏感 git 命令被拒绝: git push --force")


def _looks_like_path(token: str) -> bool:
    """识别明显像路径的 token，用于做越界前置检查。"""
    if not token or token.startswith("-"):
        return False
    if "://" in token:
        return False
    if re.match(r"^/[A-Za-z?]$", token):
        return False
    if token in (".", ".."):
        return True
    if token.startswith(("~", "/", "\\")):
        return True
    if re.match(r"^[A-Za-z]:[\\/]", token):
        return True
    return ("/" in token) or ("\\" in token)


def _validate_command_workspace(command: str, workspace: Path) -> None:
    """拒绝明显指向 workspace 外部的路径参数。"""
    for token in _tokenize_command(command):
        if not _looks_like_path(token):
            continue

        candidate = Path(token).expanduser()
        if not candidate.is_absolute():
            candidate = workspace / candidate
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(workspace)
        except ValueError as e:
            raise PermissionError(f"命令包含工作区外路径: {token}") from e


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
        workspace = _workspace_from_context(context)
        _validate_sensitive_command(command)
        _validate_command_workspace(command, workspace)
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
