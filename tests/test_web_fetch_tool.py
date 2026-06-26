from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotclaw.tools.builtin.web_fetch_tool import _ReadableHTMLParser, _validate_url


class TestWebFetchParser(unittest.TestCase):
    def test_extracts_title_and_visible_text(self):
        parser = _ReadableHTMLParser()
        parser.feed("""
        <html>
          <head><title>Example Page</title><script>ignore()</script></head>
          <body>
            <h1>Heading</h1>
            <p>Hello <b>world</b></p>
            <style>.x { color: red; }</style>
          </body>
        </html>
        """)

        self.assertEqual("Example Page", " ".join(parser.title_parts))
        text = " ".join(parser.text_parts)
        self.assertIn("Heading", text)
        self.assertIn("Hello", text)
        self.assertIn("world", text)
        self.assertNotIn("ignore", text)

    def test_rejects_non_http_url(self):
        with self.assertRaises(ValueError):
            _validate_url("file:///tmp/a.txt")


if __name__ == "__main__":
    unittest.main()
