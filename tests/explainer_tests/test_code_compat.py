"""Tests for explainer.codegen.code_compat -- the manim 0.20.1 Code API wrapper.

Imports manim (heavy), so this is a focused integration check of the
version-fragile accessor, not a pure unit test.
"""

import importlib.util
import unittest


@unittest.skipUnless(importlib.util.find_spec("manim"), "manim not installed")
class CodeCompatTests(unittest.TestCase):
    def test_build_code_and_per_line_access(self):
        from explainer.codegen.code_compat import build_code, code_line, code_line_count

        code = build_code("let a = 1;\nlet b = 2;\nlet c = 3;", language="rust")
        self.assertEqual(code_line_count(code), 3)
        # 1-based access returns a real, distinct mobject per line
        first = code_line(code, 1)
        third = code_line(code, 3)
        self.assertIsNotNone(first)
        self.assertIsNot(first, third)


if __name__ == "__main__":
    unittest.main()
