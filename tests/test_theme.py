"""Tests for app.ui.theme.load_stylesheet (pure file I/O, no Qt needed)."""

import unittest

from app.ui import theme


class LoadStylesheetTests(unittest.TestCase):
    def test_loads_the_real_theme(self):
        css = theme.load_stylesheet()
        self.assertIn("QPushButton", css)  # sanity: real theme.qss content

    def test_missing_file_degrades_to_empty(self):
        # A missing packaged stylesheet must NOT crash MainView construction
        # (which happens after QApplication exists); degrade to unstyled instead.
        self.assertEqual(theme.load_stylesheet("/no/such/theme.qss"), "")

    def test_monospace_font_prefers_macos_first(self):
        # Monaco (macOS) before Consolas (Windows-only) avoids the offscreen
        # "missing font" warning on macOS while still falling back on Windows.
        css = theme.load_stylesheet()
        self.assertLess(css.index("Monaco"), css.index("Consolas"))


if __name__ == "__main__":
    unittest.main()
