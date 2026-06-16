"""上下文压缩器测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotclaw.agent.compressor import ContextCompressor
from dotclaw.agent.agent import Agent, AgentConfig as RuntimeAgentConfig
from dotclaw.agent.context import AgentContext
from dotclaw.config.settings import Config
from dotclaw.llm.base import ChatChunk, Message
from dotclaw.memory.store import Session, SessionMessage


class FakeLLM:
    """记录摘要请求并返回固定摘要。"""

    def __init__(self, summary: str = "合并后的历史摘要"):
        self.summary = summary
        self.calls: list[list[Message]] = []

    async def chat(
        self,
        messages,
        tools=None,
        model=None,
        purpose="chat",
        stream=True,
    ):
        self.calls.append(messages)
        yield ChatChunk(content=self.summary, is_final=True, finish_reason="stop")


class TestContextCompressor(unittest.IsolatedAsyncioTestCase):
    async def test_no_overflow_returns_original_messages(self):
        llm = FakeLLM()
        compressor = ContextCompressor(llm)
        messages = [
            Message(role="system", content="系统"),
            Message(role="user", content="你好"),
        ]

        result = await compressor.compress(
            messages=messages,
            max_tokens=1000,
            keep_recent=4,
        )

        self.assertFalse(result.compressed)
        self.assertEqual(messages, result.messages)
        self.assertEqual([], llm.calls)

    async def test_overflow_summarizes_only_unsummarized_old_messages(self):
        llm = FakeLLM("已有摘要 + 新摘要")
        compressor = ContextCompressor(llm)
        history = [
            Message(role="user", content="已摘要消息0" * 20),
            Message(role="assistant", content="已摘要消息1" * 20),
            Message(role="user", content="新旧消息2" * 20),
            Message(role="assistant", content="新旧消息3" * 20),
            Message(role="user", content="最近消息4" * 20),
            Message(role="assistant", content="最近消息5" * 20),
        ]
        current = Message(role="user", content="当前问题")
        messages = [Message(role="system", content="系统提示")] + history + [current]

        result = await compressor.compress(
            messages=messages,
            max_tokens=220,
            keep_recent=2,
            existing_summary="已有摘要",
            summary_message_count=2,
            model="test-model",
        )

        self.assertTrue(result.compressed)
        self.assertEqual("已有摘要 + 新摘要", result.summary)
        self.assertEqual(4, result.summary_message_count)
        self.assertEqual(1, len(llm.calls))

        summary_prompt = llm.calls[0][1].content
        self.assertIn("已有摘要", summary_prompt)
        self.assertNotIn("已摘要消息0", summary_prompt)
        self.assertIn("新旧消息2", summary_prompt)
        self.assertIn("新旧消息3", summary_prompt)

        self.assertEqual("system", result.messages[0].role)
        self.assertIn("[较早历史对话摘要]", result.messages[1].content)
        self.assertEqual(history[-2:], result.messages[2:4])
        self.assertEqual(current, result.messages[-1])

    async def test_existing_summary_reused_when_no_new_old_messages(self):
        llm = FakeLLM()
        compressor = ContextCompressor(llm)
        history = [
            Message(role="user", content="已摘要消息0" * 20),
            Message(role="assistant", content="已摘要消息1" * 20),
            Message(role="user", content="最近消息2" * 20),
            Message(role="assistant", content="最近消息3" * 20),
        ]
        current = Message(role="user", content="当前问题")
        messages = [Message(role="system", content="系统提示")] + history + [current]

        result = await compressor.compress(
            messages=messages,
            max_tokens=220,
            keep_recent=2,
            existing_summary="已有摘要",
            summary_message_count=2,
        )

        self.assertTrue(result.compressed)
        self.assertEqual([], llm.calls)
        self.assertIn("已有摘要", result.messages[1].content)
        self.assertEqual(current, result.messages[-1])


class TestAgentContextCompressionIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_build_messages_updates_session_summary_when_over_budget(self):
        llm = FakeLLM("项目讨论摘要")
        config = Config()
        config.agent.max_context_tokens = 160
        config.agent.keep_recent_messages = 1
        agent = Agent(
            agent_config=RuntimeAgentConfig(),
            config=config,
            llm=llm,
            session_mgr=object(),
            prompt_builder=None,
        )
        agent.session = Session(
            id="1234abcd",
            title="测试会话",
            created_at="2026-06-17T00:00:00",
            updated_at="2026-06-17T00:00:00",
            messages=[
                SessionMessage(role="user", content="旧问题" * 80),
                SessionMessage(role="assistant", content="旧回答" * 80),
                SessionMessage(role="user", content="旧约束" * 80),
                SessionMessage(role="assistant", content="旧结论" * 80),
                SessionMessage(role="assistant", content="最近回答"),
            ],
        )
        context = AgentContext(
            session_id="1234abcd",
            workspace=Path.cwd(),
            project_root=Path.cwd(),
            model="test-model",
            system_prompt="系统提示",
            max_context_tokens=config.agent.max_context_tokens,
        )

        messages = await agent._build_messages("当前问题", context)

        self.assertEqual("项目讨论摘要", agent.session.summary)
        self.assertEqual(4, agent.session.summary_message_count)
        self.assertIn("[较早历史对话摘要]", messages[1].content)
        self.assertEqual("最近回答", messages[-2].content)
        self.assertEqual("当前问题", messages[-1].content)


if __name__ == "__main__":
    unittest.main()
