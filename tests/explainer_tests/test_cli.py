"""Tests for explainer.cli exit codes -- the contract callers/CI depend on.

Exit codes: 0 ok, 2 schema/validation error (including a storyboard that codegen
rejects), 3 citation miss, 4 render failure. The 0/2/3 paths are
reachable WITHOUT a real manim (verify only reads source and runs pure codegen), so
they get a regression guard here; the verifier is faked wherever a storyboard cites
real code, to keep them deterministic.
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


# A storyboard that loads and has nothing to cite (so the real gate passes)
# but cannot become a Manim scene: codegen rejects one part of the plot spec.
_REJECTED_BY_CODEGEN = """\
version: 1
meta:
  title: "Bad plot"
  source:
    repo_root: "."
beats:
  - id: b1
    canvas:
      kind: plot
      spec:
{spec}
    narration: "A curve."
"""
_BAD_FN = """\
        curves:
          - fn: "__import__('os').system('echo pwned')"
"""
_BAD_ANNOTATION = """\
        curves:
          - fn: "x"
        annotations:
          - kind: vline
            y: 1
"""
# Unquoted, YAML reads these bounds as dates, not numbers.
_BAD_AXIS = """\
        x: {min: 2020-01-01, max: 2021-01-01}
        curves:
          - fn: "x"
"""
_BAD_CURVE = """\
        curves: [5]
"""


def _run_capturing(argv):
    """Invoke the CLI; return ``(exit code, stdout, stderr)``."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class CliCodegenErrorTests(unittest.TestCase):
    """A storyboard that passes the gate but that codegen rejects is a
    validation error (exit 2, one `error:` line), reported by verify, which
    needs no manim, and by render without a traceback."""

    CASES = (
        ("curve fn", _BAD_FN, "error: unsafe/invalid curve fn"),
        ("annotation kind", _BAD_ANNOTATION, "error: unsupported annotation kind 'vline'"),
        ("axis bound", _BAD_AXIS, "error: beat 'b1': plot x.min must be a number, got "),
        ("curve entry", _BAD_CURVE, "error: beat 'b1': plot curves[0] must be a mapping, got 5"),
    )

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="explainer_cli_")
        self.addCleanup(temp.cleanup)
        self.dir = temp.name

    def _storyboard(self, spec):
        path = os.path.join(self.dir, "bad.story.yaml")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(_REJECTED_BY_CODEGEN.format(spec=spec.rstrip("\n")))
        return path

    def test_verify_returns_2_when_codegen_rejects_the_storyboard(self):
        for name, spec, message in self.CASES:
            with self.subTest(name):
                code, out, err = _run_capturing(["verify", self._storyboard(spec)])
                self.assertEqual(code, 2)
                self.assertIn(message, err)
                self.assertNotIn("Traceback", err)
                self.assertIn("citation gate OK", out)  # the gate itself passed
                self.assertNotIn("scene: OK", out)

    def test_render_returns_2_before_any_render_runs(self):
        for name, spec, message in self.CASES:
            with self.subTest(name):
                with patch("explainer.render.adapter.RenderRunner") as runner:
                    code, _, err = _run_capturing(
                        ["render", self._storyboard(spec), "-o", os.path.join(self.dir, "out.mp4")]
                    )
                self.assertEqual(code, 2)
                self.assertIn(message, err)
                self.assertNotIn("Traceback", err)
                runner.assert_not_called()

    def test_verify_reports_a_gate_failure_first_and_skips_codegen(self):
        failures = [CiteFailure("b1", "x.py:1", ("x",))]
        with patch("explainer.cli.verify_storyboard", return_value=failures):
            code, _, err = _run_capturing(["verify", self._storyboard(_BAD_ANNOTATION)])
        self.assertEqual(code, 3)
        self.assertIn("citation gate FAILED", err)
        self.assertNotIn("annotation", err)

    def test_verify_ok_says_the_scene_code_was_generated(self):
        with patch("explainer.cli.verify_storyboard", return_value=[]):
            code, out, err = _run_capturing(["verify", EXAMPLE])
        self.assertEqual(code, 0)
        self.assertIn("scene: OK (Manim scene code generated for 3 beat(s))", out)
        self.assertEqual(err, "")


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
