import contextlib
import io
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

from app.environment.__main__ import inspect_environment, main


class AgentPreflightTests(unittest.TestCase):
    def probe(self, missing=(), broken=(), require_latex=False, manim=True):
        def which(name):
            return None if name in missing else "/tools/" + name

        def run(argv, timeout):
            name = argv[0].split("/")[-1]
            return subprocess.CompletedProcess(argv, 1 if name in broken else 0, "1.2.3", "")

        return inspect_environment(require_latex, which, run, lambda _: object() if manim else None)

    def test_ready_without_build_metadata(self):
        result = self.probe(missing=("pkg-config", "latex", "dvisvgm"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["render_smoke_test"], "not_run")
        self.assertEqual(result["build_diagnostics"][0]["status"], "warning")

    def test_missing_manim(self):
        result = self.probe(manim=False)
        self.assertEqual(result["blockers"], ["manim"])
        self.assertEqual(result["recovery"]["manim_install_argv"][0], sys.executable)

    def test_missing_or_broken_media_tools(self):
        for tool in ("ffmpeg", "ffprobe"):
            for option in ("missing", "broken"):
                with self.subTest(tool=tool, option=option):
                    self.assertIn(tool, self.probe(**{option: (tool,)})["blockers"])

    def test_default_tex_requires_both_tools(self):
        for tool in ("latex", "dvisvgm"):
            self.assertIn(tool, self.probe(missing=(tool,), require_latex=True)["blockers"])
        self.assertTrue(self.probe(require_latex=True)["ok"])

    def test_json_and_exit(self):
        for ok in (True, False):
            result = self.probe(manim=ok)
            with patch("app.environment.__main__.inspect_environment", return_value=result):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(main(["--json"]), 0 if ok else 1)
                self.assertEqual(json.loads(out.getvalue())["ok"], ok)

    def test_invalid_argument(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
            main(["--unknown"])
        self.assertEqual(exc.exception.code, 2)

    def test_broken_tex_and_timeout(self):
        for tool in ("latex", "dvisvgm"):
            self.assertIn(tool, self.probe(broken=(tool,), require_latex=True)["blockers"])

        def timeout(argv, seconds):
            raise subprocess.TimeoutExpired(argv, seconds)

        report = inspect_environment(
            run=timeout, which=lambda n: "/bin/" + n, find_spec=lambda n: object()
        )
        self.assertIn("manim", report["blockers"])
        self.assertIn("ffprobe", report["blockers"])

    def test_pdflatex_only_does_not_satisfy_default_tex(self):
        report = self.probe(missing=("latex",), require_latex=True)
        self.assertIn("latex", report["blockers"])

    def test_generic_install_advice_removed(self):
        report = self.probe(manim=False)
        self.assertNotIn("pip install manim", report["checks"][1]["detail"])
