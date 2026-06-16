"""上下文压缩器：旧消息 LLM 摘要 + 最近消息窗口。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..llm.base import Message
from .message_utils import _msg_tokens, trim as msg_trim

if TYPE_CHECKING:
    from ..llm.proxy import LLMProxy


SUMMARY_SYSTEM_PROMPT = """你是会话上下文压缩器。
你会收到：
1. 已有历史摘要（可能为空）
2. 本次需要合并进摘要的较早对话

请输出一段中文摘要，用于后续 AI 继续理解会话背景。

摘要必须保留：
- 用户偏好、长期目标、约束
- 已经做出的决定和结论
- 重要文件路径、函数名、类名、命令、错误信息
- 未完成事项和后续计划
- 工具调用得出的关键事实

摘要必须避免：
- 问候、寒暄、重复确认
- 无信息量的细节
- 编造未出现的信息

只输出摘要正文，不要输出 JSON，不要加 Markdown 标题。"""


@dataclass
class CompressionResult:
    """上下文压缩结果。"""

    messages: list[Message]
    summary: str
    summary_message_count: int
    compressed: bool


def estimate_messages_tokens(messages: list[Message]) -> int:
    """使用项目现有估算方式统计消息 token。"""
    return sum(_msg_tokens(m) for m in messages)


class ContextCompressor:
    """将较早会话消息压缩为 session.summary，保留最近消息原文。"""

    def __init__(self, llm: "LLMProxy"):
        self._llm = llm

    async def compress(
        self,
        messages: list[Message],
        max_tokens: int,
        keep_recent: int,
        existing_summary: str = "",
        summary_message_count: int = 0,
        model: str | None = None,
        purpose: str = "chat",
    ) -> CompressionResult:
        """压缩上下文。

        输入 messages 约定为：system + 历史消息 + 当前用户输入。
        输出 messages 约定为：system + 历史摘要 + 最近消息 + 当前用户输入。
        """
        if estimate_messages_tokens(messages) <= max_tokens:
            return CompressionResult(
                messages=messages,
                summary=existing_summary,
                summary_message_count=summary_message_count,
                compressed=False,
            )

        if len(messages) <= 2:
            return CompressionResult(
                messages=msg_trim(messages, max_tokens),
                summary=existing_summary,
                summary_message_count=summary_message_count,
                compressed=False,
            )

        system_msgs = [m for m in messages if m.role == "system"]
        body_msgs = [m for m in messages if m.role != "system"]
        current_user = body_msgs[-1]
        history = body_msgs[:-1]

        keep_recent = max(0, keep_recent)
        cutoff = max(0, len(history) - keep_recent)
        already_summarized = max(0, min(summary_message_count, cutoff))
        summary_message_count = already_summarized
        new_messages_to_summarize = history[already_summarized:cutoff]

        summary = existing_summary.strip()
        if new_messages_to_summarize:
            summary = await self._summarize(
                existing_summary=summary,
                messages=new_messages_to_summarize,
                model=model,
                purpose=purpose,
            )
            summary_message_count = cutoff

        compressed_messages = self._assemble(
            system_msgs=system_msgs,
            summary=summary,
            recent_messages=history[cutoff:],
            current_user=current_user,
        )

        if estimate_messages_tokens(compressed_messages) > max_tokens:
            compressed_messages = msg_trim(compressed_messages, max_tokens)

        return CompressionResult(
            messages=compressed_messages,
            summary=summary,
            summary_message_count=summary_message_count,
            compressed=True,
        )

    async def _summarize(
        self,
        existing_summary: str,
        messages: list[Message],
        model: str | None,
        purpose: str,
    ) -> str:
        """调用 LLM 生成合并后的历史摘要。"""
        prompt = (
            "已有历史摘要：\n"
            f"{existing_summary if existing_summary else '（空）'}\n\n"
            "需要合并进摘要的较早对话：\n"
            f"{self._format_messages(messages)}"
        )
        llm_messages = [
            Message(role="system", content=SUMMARY_SYSTEM_PROMPT),
            Message(role="user", content=prompt),
        ]

        chunks: list[str] = []
        async for chunk in self._llm.chat(
            messages=llm_messages,
            tools=None,
            model=model,
            purpose=purpose,
            stream=False,
        ):
            if chunk.content:
                chunks.append(chunk.content)
            if chunk.is_final:
                break

        summary = "".join(chunks).strip()
        if not summary:
            raise RuntimeError("上下文摘要为空")
        return summary

    @staticmethod
    def _assemble(
        system_msgs: list[Message],
        summary: str,
        recent_messages: list[Message],
        current_user: Message,
    ) -> list[Message]:
        """组装压缩后的消息列表。"""
        result = list(system_msgs)
        if summary:
            result.append(Message(
                role="user",
                content="[较早历史对话摘要]\n" + summary,
            ))
        result.extend(recent_messages)
        result.append(current_user)
        return result

    @staticmethod
    def _format_messages(messages: list[Message]) -> str:
        """把消息列表转成摘要模型更容易处理的文本。"""
        lines: list[str] = []
        for msg in messages:
            label = {
                "system": "系统",
                "user": "用户",
                "assistant": "助手",
                "tool": "工具",
            }.get(msg.role, msg.role)
            lines.append(f"{label}: {msg.content}")
            if msg.tool_calls:
                for call in msg.tool_calls:
                    lines.append(f"助手工具调用: {call.name}({call.arguments})")
            if msg.tool_call_id:
                lines.append(f"工具调用ID: {msg.tool_call_id}")
        return "\n".join(lines)
