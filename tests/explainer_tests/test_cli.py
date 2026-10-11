"""Tests for explainer.cli exit codes -- the contract callers/CI depend on.

Exit codes: 0 ok, 2 schema/validation error, 3 citation miss, 4 render failure.
The 0/2/3 paths are reachable WITHOUT a real manim (verify only reads source), so
they get a regression guard here; the verifier is faked to keep them deterministic.
"""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from explainer.cite.verifier import CiteFailure
from explainer.cli import main

EXAMPLE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "examples",
    "evidence-budget.story.yaml",
)


def _run(argv):
    """Invoke the CLI, swallowing its stdout/stderr; return the exit code."""
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return main(argv)


class CliExitCodeTests(unittest.TestCase):
    def test_verify_ok_returns_0(self):
        with patch("explainer.cli.verify_storyboard", return_value=[]):
            self.assertEqual(_run(["verify", EXAMPLE]), 0)

    def test_verify_ok_lists_each_cite(self):
        # decision 2: the live offline trust moment must SHOW the per-fact proof, not
        # just a summary line, so an audience sees each cite that was verified.
        out = io.StringIO()
        with patch("explainer.cli.verify_storyboard", return_value=[]):
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                code = main(["verify", EXAMPLE])
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn("explainer/plan/evidence.py:56", text)
        self.assertIn("explainer/plan/evidence.py:205-211", text)
        self.assertIn("explainer/plan/evidence.py:55-57", text)

    def test_verify_citation_miss_returns_3(self):
        failures = [CiteFailure("floor-and-cap", "evidence.py:205-211", ("24",))]
        with patch("explainer.cli.verify_storyboard", return_value=failures):
            self.assertEqual(_run(["verify", EXAMPLE]), 3)

    def test_schema_error_returns_2(self):
        path = os.path.join(tempfile.mkdtemp(prefix="explainer_cli_"), "bad.story.yaml")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("version: 99\nmeta: {title: t}\nbeats: []\n")
        self.assertEqual(_run(["verify", path]), 2)


class QualityFlagNormalizationTests(unittest.TestCase):
    """--quality must accept human forms, because the natural spelling
    `--quality -ql` is eaten by argparse (the dash starts a new option) and the
    only working dashed spelling, `--quality=-ql`, is undiscoverable."""

    def _render_quality(self, value, equals=False):
        """Run `render --quality <value>` (or `--quality=<value>` with
        ``equals=True``) with a stubbed renderer; return the quality it got."""
        seen = {}

        def fake_render(storyboard, output, quality=None, on_log=None):
            seen["quality"] = quality
            return output

        argv = ["render", EXAMPLE, "-o", "/tmp/out.mp4"]
        argv += [f"--quality={value}"] if equals else ["--quality", value]
        with patch("explainer.cli.render_storyboard", new=fake_render):
            code = _run(argv)
        self.assertEqual(code, 0)
        return seen["quality"]

    def test_bare_name_is_normalized_to_flag(self):
        self.assertEqual(self._render_quality("ql"), "-ql")
        self.assertEqual(self._render_quality("qp"), "-qp")

    def test_dashed_flag_works_only_in_equals_form(self):
        # `--quality -qh` (separate tokens) is structurally unparseable: argparse
        # consumes -qh as a new option before any type conversion runs. The
        # equals form must keep working.
        self.assertEqual(self._render_quality("-qh", equals=True), "-qh")

    def test_resolution_height_is_mapped_via_ladder(self):
        self.assertEqual(self._render_quality("1080p"), "-qh")
        self.assertEqual(self._render_quality("1440p60"), "-qp")
        self.assertEqual(self._render_quality("480"), "-ql")

    def test_unknown_quality_is_rejected_with_exit_2(self):
        with self.assertRaises(SystemExit) as cm:
            _run(["render", EXAMPLE, "-o", "/tmp/out.mp4", "--quality", "potato"])
        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
