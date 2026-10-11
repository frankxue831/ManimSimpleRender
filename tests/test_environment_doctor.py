"""Tests for app.environment.doctor (Qt-free environment preflight).

The doctor reports whether the CURRENT interpreter can render: manim is EXERCISED
(``-m manim --version``), not just located, so a broken install is caught (BL-07);
ffmpeg/LaTeX report their version and warn if present-but-unrunnable (BL-34). The
subprocess runner is injectable so these run headlessly with no real tools.
Run: python3 -m unittest tests.test_environment_doctor
"""

import subprocess
import unittest

from app.environment.doctor import (
    check_environment,
    check_ffmpeg,
    check_latex,
    check_manim,
    check_python,
)
from app.environment.models import CheckStatus, EnvironmentCheck, EnvironmentReport


class _Result:
    """A minimal subprocess.CompletedProcess stand-in."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _runs(returncode=0, stdout="", stderr=""):
    return lambda argv, timeout: _Result(returncode, stdout, stderr)


def _raises(exc):
    def run(*args, **kwargs):  # usable as both find_spec(name) and run(argv, timeout)
        raise exc

    return run


class CheckManimTests(unittest.TestCase):
    def test_present_and_runs_reports_ok_with_version(self):
        c = check_manim(find_spec=lambda n: object(), run=_runs(0, "Manim Community v0.18.1\n"))
        self.assertIs(c.status, CheckStatus.OK)
        self.assertIn("0.18.1", c.detail)

    def test_absent_reports_failed_without_running(self):
        ran = []
        c = check_manim(find_spec=lambda n: None, run=lambda a, t: ran.append(a) or _Result())
        self.assertIs(c.status, CheckStatus.FAILED)
        self.assertEqual(ran, [])  # short-circuit: never shells out when not installed

    def test_locatable_but_nonzero_exit_is_failed(self):
        # find_spec succeeds but the interpreter cannot actually run manim (BL-07).
        c = check_manim(find_spec=lambda n: object(), run=_runs(1, "", "boom"))
        self.assertIs(c.status, CheckStatus.FAILED)
        self.assertIn("boom", c.detail)

    def test_importerror_in_stderr_is_failed_even_on_zero_exit(self):
        c = check_manim(
            find_spec=lambda n: object(),
            run=_runs(0, "", "ImportError: No module named 'pycairo'"),
        )
        self.assertIs(c.status, CheckStatus.FAILED)
        self.assertIn("ImportError", c.detail)

    def test_executable_missing_is_failed(self):
        c = check_manim(find_spec=lambda n: object(), run=_raises(FileNotFoundError()))
        self.assertIs(c.status, CheckStatus.FAILED)

    def test_timeout_is_failed(self):
        c = check_manim(
            find_spec=lambda n: object(), run=_raises(subprocess.TimeoutExpired("manim", 1))
        )
        self.assertIs(c.status, CheckStatus.FAILED)
        self.assertIn("timed out", c.detail.lower())

    def test_find_spec_raising_is_treated_as_absent(self):
        c = check_manim(find_spec=_raises(ModuleNotFoundError()), run=_runs(0, "v0.18.1"))
        self.assertIs(c.status, CheckStatus.FAILED)

    def test_frozen_runs_external_manim_on_path(self):
        ok = check_manim(
            frozen=True, which=lambda n: "/usr/local/bin/manim", run=_runs(0, "v0.18.1")
        )
        self.assertIs(ok.status, CheckStatus.OK)
        missing = check_manim(frozen=True, which=lambda n: None, run=_runs(0, "v0.18.1"))
        self.assertIs(missing.status, CheckStatus.FAILED)


class CheckPythonTests(unittest.TestCase):
    def test_non_frozen_reports_interpreter_path(self):
        c = check_python(frozen=False)
        self.assertIs(c.status, CheckStatus.OK)
        self.assertIn("at ", c.detail)

    def test_frozen_relabels_as_app_runtime(self):
        # BL-34: in a frozen app sys.executable is the GUI binary, not the renderer.
        c = check_python(frozen=True)
        self.assertIn("external manim", c.detail.lower())


class CheckFfmpegTests(unittest.TestCase):
    def test_present_and_runnable_ok_with_version(self):
        c = check_ffmpeg(which=lambda n: "/usr/bin/ffmpeg", run=_runs(0, "ffmpeg version 6.0\n"))
        self.assertIs(c.status, CheckStatus.OK)
        self.assertIn("6.0", c.detail)

    def test_present_but_unrunnable_is_warning(self):
        # BL-34: a present-but-broken ffmpeg should warn, not silently report OK.
        c = check_ffmpeg(which=lambda n: "/usr/bin/ffmpeg", run=_raises(OSError("denied")))
        self.assertIs(c.status, CheckStatus.WARNING)

    def test_absent_failed(self):
        c = check_ffmpeg(which=lambda n: None, run=_runs(0))
        self.assertIs(c.status, CheckStatus.FAILED)


class CheckLatexTests(unittest.TestCase):
    def test_present_ok_with_version(self):
        c = check_latex(
            which=lambda n: "/usr/bin/pdflatex" if n == "pdflatex" else None,
            run=_runs(0, "pdfTeX 3.141592653-2.6-1.40.25\n"),
        )
        self.assertIs(c.status, CheckStatus.OK)

    def test_present_but_unrunnable_is_warning(self):
        c = check_latex(which=lambda n: "/usr/bin/latex", run=_runs(1, "", "broken"))
        self.assertIs(c.status, CheckStatus.WARNING)

    def test_absent_is_warning_not_failed(self):
        c = check_latex(which=lambda n: None, run=_runs(0))
        self.assertIs(c.status, CheckStatus.WARNING)


class CheckEnvironmentTests(unittest.TestCase):
    def _which_all(self, name):
        return "/x/" + name

    def test_all_present_report_ok(self):
        report = check_environment(
            find_spec=lambda n: object(), which=self._which_all, run=_runs(0, "v0.18.1")
        )
        self.assertIsInstance(report, EnvironmentReport)
        self.assertTrue(report.ok)
        self.assertEqual({c.name for c in report.checks}, {"Python", "manim", "ffmpeg", "LaTeX"})

    def test_broken_manim_fails_report(self):
        report = check_environment(
            find_spec=lambda n: object(), which=self._which_all, run=_runs(1, "", "Traceback ...")
        )
        self.assertFalse(report.ok)

    def test_missing_latex_only_is_warning_not_failure(self):
        def which(name):
            return None if name in ("latex", "pdflatex") else "/x/" + name

        report = check_environment(
            find_spec=lambda n: object(), which=which, run=_runs(0, "v0.18.1")
        )
        self.assertTrue(report.ok)


class IssueTextTests(unittest.TestCase):
    def test_issue_text_lists_only_actionable_lines(self):
        # BL-09: the always-visible body should carry the FAILED/WARNING lines only.
        report = EnvironmentReport(
            [
                EnvironmentCheck("Python", CheckStatus.OK, "3.12"),
                EnvironmentCheck("manim", CheckStatus.FAILED, "not installed (pip install manim)"),
                EnvironmentCheck("LaTeX", CheckStatus.WARNING, "not found"),
            ]
        )
        issues = report.issue_text()
        self.assertIn("manim", issues)
        self.assertIn("LaTeX", issues)
        self.assertNotIn("Python", issues)  # OK lines stay in the full dump only

    def test_issue_text_empty_when_all_ok(self):
        report = EnvironmentReport([EnvironmentCheck("manim", CheckStatus.OK, "0.18.1")])
        self.assertEqual(report.issue_text(), "")


if __name__ == "__main__":
    unittest.main()
