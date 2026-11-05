"""Actionable bounded font hints without echoing arbitrary render-log content."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from app.render.aggregation import JobOutcome
from app.render.runner import JobResult
from scene_production import render
from scene_production.__main__ import _error


class FontFailureDiagnosticsTests(unittest.TestCase):
    def fail_render(self, output):
        class Failed:
            def run_job(self, job, command, event, log, env):
                log(output)
                return JobResult(job, 1, JobOutcome.FAILED)

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        source = root / "scene.py"
        source.write_text(
            "from manim import Scene\nclass Demo(Scene):\n def construct(self): pass\n"
        )
        timeline = root / "timeline.json"
        timeline.write_text('{"version":1,"segments":[{"label":"intro","start":0,"end":1}]}')
        with self.assertRaises(RuntimeError) as result:
            render(source, "Demo", timeline, root / "out", runner=Failed())
        return result.exception, next((root / "out").glob("*/result.json")).resolve()

    def test_missing_glyph_hint_is_visible_without_echoing_log_value(self):
        exc, receipt = self.fail_render(
            "ValueError: SCENE_FONT has missing glyphs: PRIVATE_LOG_VALUE"
        )
        self.assertIn("selected font lacks required glyphs", str(exc))
        self.assertNotIn("PRIVATE_LOG_VALUE", str(exc))
        self.assertIn(
            "selected font lacks required glyphs", json.loads(receipt.read_text())["error"]
        )
        self.assertIn("PRIVATE_LOG_VALUE", receipt.with_name("render.log").read_text())

    def test_missing_font_hint_handles_terminal_colour(self):
        exc, _ = self.fail_render(
            "\x1b[31mValueError: SCENE_FONT is not installed: unavailable\x1b[0m"
        )
        self.assertIn("selected font is not installed", str(exc))

    def test_only_final_exception_line_supplies_hint(self):
        for text in [
            'raise ValueError("SCENE_FONT has missing glyphs: " + requested)\nRuntimeError: other',
            "ValueError: SCENE_FONT has missing glyphs: Arial\nRuntimeError: other",
            "ValueError: SCENE_FONT has missing glyphs: Arial\n" + "x" * 70000,
        ]:
            with self.subTest(text=text[:40]):
                exc, _ = self.fail_render(text)
                self.assertNotIn("font hint", str(exc))

    def test_cli_points_to_preserved_log_without_dumping_it(self):
        exc, receipt = self.fail_render("RuntimeError: PRIVATE_LOG_VALUE")
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            _error(exc)
        self.assertIn("render log: " + str(receipt.with_name("render.log")), output.getvalue())
        self.assertNotIn("PRIVATE_LOG_VALUE", output.getvalue())
