"""web_search 内置工具测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotclaw.tools.builtin import register_all
from dotclaw.tools.builtin.web_search_tool import _parse_results, web_search
from dotclaw.tools.registry import ToolRegistry


class TestWebSearchTool(unittest.IsolatedAsyncioTestCase):
    async def test_empty_query_returns_error(self):
        result = await web_search("   ")
        self.assertIn("query 不能为空", result)


class TestWebSearchParser(unittest.TestCase):
    def test_parse_duckduckgo_lite_results(self):
        html = """
        <html>
          <body>
            <a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">
              Example A
            </a>
            <a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fb">
              Example B
            </a>
            <a href="/lite/">DuckDuckGo internal</a>
          </body>
        </html>
        """

        results = _parse_results(html, max_results=5)

        self.assertEqual(
            [
                {"title": "Example A", "url": "https://example.com/a"},
                {"title": "Example B", "url": "https://example.com/b"},
            ],
            results,
        )

    def test_parse_results_deduplicates_urls(self):
        html = """
        <a href="https://example.com/a">First</a>
        <a href="https://example.com/a">Duplicate</a>
        """

        results = _parse_results(html, max_results=5)

        self.assertEqual([{"title": "First", "url": "https://example.com/a"}], results)


class TestWebSearchRegistration(unittest.TestCase):
    def test_register_all_can_include_web_search(self):
        registry = ToolRegistry()
        register_all(registry, include_web_search=True)

        names = set(registry.all_names())

        self.assertIn("web_search", names)

    def test_register_all_can_disable_web_search(self):
        registry = ToolRegistry()
        register_all(registry, include_web_search=False)

        names = set(registry.all_names())

        self.assertNotIn("web_search", names)


if __name__ == "__main__":
    unittest.main()
