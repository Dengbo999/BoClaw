"""内置工具 workspace 边界测试。"""

from __future__ import annotations

import sys
import tempfile
import unittest
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotclaw.tools.approval import ApprovalManager
from dotclaw.tools.base import ToolExecutionContext
from dotclaw.tools.builtin.exec_tool import exec_command
from dotclaw.tools.builtin.file_tool import list_dir, read_file, write_file
from dotclaw.tools.builtin.file_tool import get_read_file_handler
from dotclaw.tools.executor import ToolExecutor
from dotclaw.tools.registry import ToolRegistry


class FakeChannel:
    """测试审批分支时使用的最小通道。"""

    def __init__(self, answer: str):
        self.answer = answer

    async def ask_user(self, prompt: str) -> str:
        return self.answer


class TestToolWorkspaceSecurity(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name) / "workspace"
        self.workspace.mkdir()
        self.outside = Path(self._tmp.name) / "outside.txt"
        self.outside.write_text("secret", encoding="utf-8")
        (self.workspace / "inside.txt").write_text("ok", encoding="utf-8")
        self.context = ToolExecutionContext(workspace=self.workspace)

    async def asyncTearDown(self):
        self._tmp.cleanup()

    async def test_read_file_rejects_outside_absolute_path(self):
        result = await read_file(str(self.outside), context=self.context)
        self.assertIn("路径超出工作区", result)

    async def test_write_file_rejects_parent_traversal(self):
        result = await write_file("../created.txt", "bad", context=self.context)
        self.assertIn("路径超出工作区", result)
        self.assertFalse((self.workspace.parent / "created.txt").exists())

    async def test_list_dir_rejects_parent_traversal(self):
        result = await list_dir("..", context=self.context)
        self.assertIn("路径超出工作区", result)

    async def test_tool_executor_passes_workspace_context(self):
        registry = ToolRegistry()
        registry.register(get_read_file_handler())
        executor = ToolExecutor(registry=registry)

        result = await executor.execute(
            "read_file",
            {"path": "inside.txt"},
            workspace=self.workspace,
        )

        self.assertFalse(result.is_error)
        self.assertEqual("ok", result.output)

    async def test_exec_rejects_outside_path(self):
        result = await exec_command("type ..\\outside.txt", context=self.context)
        self.assertIn("工作区外路径", result)

    async def test_exec_rejects_sensitive_command(self):
        result = await exec_command("del inside.txt", context=self.context)
        self.assertIn("敏感命令被拒绝", result)
        self.assertTrue((self.workspace / "inside.txt").exists())

    async def test_approval_audit_log_redacts_sensitive_arguments(self):
        audit_path = self.workspace / "approvals.jsonl"
        approval = ApprovalManager(["exec"], audit_log_path=audit_path)

        approved = await approval.check(
            "exec",
            {"command": "echo ok", "api_key": "secret-value"},
            channel=FakeChannel("y"),
        )

        self.assertTrue(approved)
        line = audit_path.read_text(encoding="utf-8").strip()
        event = json.loads(line)
        self.assertEqual("approved", event["decision"])
        self.assertEqual("***REDACTED***", event["arguments"]["api_key"])


if __name__ == "__main__":
    unittest.main()
