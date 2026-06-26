"""网页抓取工具（builtin 子包）。"""

from __future__ import annotations

import asyncio
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from html import unescape
from html.parser import HTMLParser

from dotclaw.tools.handler import BuiltinToolHandler

DEFAULT_TIMEOUT = 12.0
DEFAULT_MAX_BYTES = 200_000
MAX_TEXT_CHARS = 20_000


class _ReadableHTMLParser(HTMLParser):
    """从 HTML 中提取标题和正文候选文本。"""

    def __init__(self) -> None:
        super().__init__()
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self._tag_stack: list[str] = []
        self._skip_depth = 0
        self._text_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        self._tag_stack.append(tag)
        if tag in {"script", "style", "noscript", "svg", "canvas"}:
            self._skip_depth += 1
        if tag in {
            "p", "article", "section", "main", "li", "h1", "h2", "h3",
            "blockquote", "td", "th", "span", "div",
        }:
            self._text_depth += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg", "canvas"} and self._skip_depth:
            self._skip_depth -= 1
        if tag in {
            "p", "article", "section", "main", "li", "h1", "h2", "h3",
            "blockquote", "td", "th", "span", "div",
        } and self._text_depth:
            self._text_depth -= 1
        if self._tag_stack:
            self._tag_stack.pop()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = _normalize_space(data)
        if not text:
            return
        current = self._tag_stack[-1] if self._tag_stack else ""
        if current == "title":
            self.title_parts.append(text)
        elif self._text_depth:
            self.text_parts.append(text)


def _normalize_space(text: str) -> str:
    return " ".join(unescape(text or "").split())


def _validate_url(url: str) -> str:
    url = (url or "").strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("url 只支持 http/https")
    if not parsed.netloc:
        raise ValueError("url 缺少域名")
    return url


def _fetch_sync(url: str, max_bytes: int) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "dotClaw/0.1 (+https://github.com/aandbcct/dotClaw)",
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8,*/*;q=0.5",
        },
    )
    with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
        final_url = response.geturl()
        content_type = response.headers.get("Content-Type", "")
        charset = response.headers.get_content_charset() or "utf-8"
        raw = response.read(max_bytes + 1)

    truncated = len(raw) > max_bytes
    raw = raw[:max_bytes]
    html = raw.decode(charset, errors="replace")

    parser = _ReadableHTMLParser()
    parser.feed(html)

    title = _normalize_space(" ".join(parser.title_parts))
    text = _normalize_space("\n".join(parser.text_parts))
    if not text:
        text = _normalize_space(re.sub(r"<[^>]+>", " ", html))
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS].rstrip() + "..."

    return {
        "url": final_url,
        "title": title,
        "content_type": content_type,
        "text": text,
        "truncated": truncated,
    }


async def fetch_url(url: str, max_bytes: int = DEFAULT_MAX_BYTES) -> dict:
    """抓取网页并返回结构化内容，供工具和研究管理器复用。"""
    url = _validate_url(url)
    try:
        max_bytes = int(max_bytes)
    except (TypeError, ValueError):
        max_bytes = DEFAULT_MAX_BYTES
    max_bytes = max(10_000, min(max_bytes, 1_000_000))
    return await asyncio.to_thread(_fetch_sync, url, max_bytes)


async def web_fetch(url: str, max_bytes: int = DEFAULT_MAX_BYTES) -> str:
    """抓取网页标题和正文，返回 JSON。"""
    try:
        payload = await fetch_url(url, max_bytes=max_bytes)
    except (ValueError, urllib.error.URLError, TimeoutError) as e:
        return f"错误：网页抓取失败 - {e}"
    except Exception as e:
        return f"错误：网页抓取异常 - {e}"
    return json.dumps(payload, ensure_ascii=False, indent=2)


def get_web_fetch_handler() -> BuiltinToolHandler:
    return BuiltinToolHandler(
        name="web_fetch",
        description=(
            "抓取指定网页的标题和正文文本。用于读取 web_search 返回的来源内容，"
            "只支持 http/https URL。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "要抓取的网页 URL，必须是 http/https",
                },
                "max_bytes": {
                    "type": "integer",
                    "description": "最大读取字节数，默认 200000",
                    "default": DEFAULT_MAX_BYTES,
                },
            },
            "required": ["url"],
        },
        handler_fn=web_fetch,
        needs_approval=False,
        timeout=DEFAULT_TIMEOUT + 5,
    )
