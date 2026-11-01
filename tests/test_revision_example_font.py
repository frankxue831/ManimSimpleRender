"""A caption edit must not bypass glyph validation through an old whitelist."""

from pathlib import Path
import runpy
import sys
from types import ModuleType
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "examples/scenes/revision_flow.py"


class RevisionExampleFontTests(unittest.TestCase):
    def test_a_new_caption_with_uncovered_glyph_is_rejected(self):
        manim = ModuleType("manim")
        for name in ("BLUE", "LEFT", "RIGHT", "WHITE", "Dot", "Transform"):
            setattr(manim, name, object())
        manim.Scene = type("Scene", (), {})

        class Text:
            width = 1

            def __init__(self, value, font, **kwargs):
                self.value, self.font = value, font

            def move_to(self, position):
                return self

        manim.Text = Text
        font_module = ModuleType("scene_font")

        def installed_font_for(required):
            if "\U0010ffff" in required:
                raise ValueError("missing glyph in edited caption")
            return "InstalledFont"

        font_module.scene_font = installed_font_for
        with patch.dict(sys.modules, {"manim": manim, "scene_font": font_module}):
            label = runpy.run_path(str(SOURCE))["label"]
            self.assertEqual(label("valid caption").font, "InstalledFont")
            with self.assertRaisesRegex(ValueError, "missing glyph"):
                label("new caption\U0010ffff")
