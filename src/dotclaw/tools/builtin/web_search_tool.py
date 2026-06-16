"""Web 搜索工具（builtin 子包）"""

from __future__ import annotations

import asyncio
import json
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from dotclaw.tools.handler import BuiltinToolHandler

SEARCH_ENDPOINT = "https://lite.duckduckgo.com/lite/"
DEFAULT_TIMEOUT = 10.0
MAX_RESULTS_LIMIT = 10


class _DuckDuckGoLiteParser(HTMLParser):
    """解析 DuckDuckGo Lite 搜索结果页中的标题和链接。"""

    def __init__(self):
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._current_href: str | None = None
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attr_map = dict(attrs)
        href = attr_map.get("href")
        normalized = _normalize_result_url(href or "")
        if normalized:
            self._current_href = normalized
            self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._current_href:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._current_href:
            return

        title = " ".join("".join(self._current_text).split())
        if title:
            self.results.append({"title": title, "url": self._current_href})

        self._current_href = None
        self._current_text = []


def _normalize_result_url(href: str) -> str | None:
    """把 DuckDuckGo 跳转链接还原成真实 URL，并过滤站内链接。"""
    if not href:
        return None
    if href.startswith("//"):
        href = "https:" + href

    parsed = urllib.parse.urlparse(href)
    query = urllib.parse.parse_qs(parsed.query)
    if "uddg" in query and query["uddg"]:
        return urllib.parse.unquote(query["uddg"][0])

    if parsed.scheme in ("http", "https") and "duckduckgo.com" not in parsed.netloc:
        return href
    return None


def _parse_results(html: str, max_results: int) -> list[dict[str, str]]:
    """从搜索 HTML 中提取去重后的结果列表。"""
    parser = _DuckDuckGoLiteParser()
    parser.feed(html)

    results: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for item in parser.results:
        url = item["url"]
        if url in seen_urls:
            continue
        seen_urls.add(url)
        results.append(item)
        if len(results) >= max_results:
            break
    return results


def _search_sync(query: str, max_results: int) -> dict:
    """同步执行搜索请求，供 asyncio.to_thread 调用。"""
    params = urllib.parse.urlencode({"q": query})
    request = urllib.request.Request(
        f"{SEARCH_ENDPOINT}?{params}",
        headers={
            "User-Agent": "dotClaw/0.1 (+https://github.com/aandbcct/dotClaw)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        html = response.read().decode(charset, errors="replace")

    return {
        "query": query,
        "results": _parse_results(html, max_results),
    }


async def web_search(query: str, max_results: int = 5) -> str:
    """搜索互联网并返回 JSON 格式的结果。"""
    query = (query or "").strip()
    if not query:
        return "错误：query 不能为空"

    try:
        max_results = int(max_results)
    except (TypeError, ValueError):
        max_results = 5
    max_results = max(1, min(max_results, MAX_RESULTS_LIMIT))

    try:
        payload = await asyncio.to_thread(_search_sync, query, max_results)
    except Exception as e:
        return f"错误：网络搜索失败 - {e}"

    if not payload["results"]:
        return json.dumps(
            {"query": query, "results": [], "message": "未找到搜索结果"},
            ensure_ascii=False,
            indent=2,
        )

    return json.dumps(payload, ensure_ascii=False, indent=2)


def get_web_search_handler() -> BuiltinToolHandler:
    return BuiltinToolHandler(
        name="web_search",
        description="搜索互联网，返回标题和链接。用于需要最新外部信息的问题。",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词",
                },
                "max_results": {
                    "type": "integer",
                    "description": f"返回结果数量，范围 1-{MAX_RESULTS_LIMIT}",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
        handler_fn=web_search,
        needs_approval=False,
        timeout=DEFAULT_TIMEOUT + 5,
    )
