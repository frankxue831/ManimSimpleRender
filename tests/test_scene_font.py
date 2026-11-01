"""Font selection fails before producing a video with missing glyphs."""

import importlib.util
from pathlib import Path
import unittest


class SceneFontTests(unittest.TestCase):
    def load(self):
        path = Path(__file__).resolve().parents[1] / "examples/scenes/scene_font.py"
        spec = importlib.util.spec_from_file_location("scene_font", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_selection_rejects_missing_and_incomplete_requested_fonts(self):
        choose = self.load().select_font
        with self.assertRaisesRegex(ValueError, "not installed"):
            choose(["Good"], lambda f: True, "Missing")
        with self.assertRaisesRegex(ValueError, "missing glyphs"):
            choose(["Bad"], lambda f: False, "Bad")
        self.assertEqual(choose(["Bad", "Good"], lambda f: f == "Good"), "Good")
        with self.assertRaisesRegex(ValueError, "SCENE_FONT"):
            choose(["Bad"], lambda f: False)
