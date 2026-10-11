"""Tier A env-doctor e2e: the BL-07 reproduction with REAL subprocesses.

Unlike the doctor unit tests (which inject a runner), these run the doctor's actual
subprocess probe against real scripts on disk: a "manim" that fails to import like a
broken install must be FAILED (not OK), and a genuinely-runnable manim probe must
parse a version. Qt-free, runs on a bare python3.
Run: python3 -m unittest tests.e2e.test_env_doctor_broken
"""

import importlib.util
import os
import stat
import sys
import tempfile
import unittest

from app.environment.doctor import check_manim
from app.environment.models import CheckStatus


def _write_executable(folder, name, body):
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


class BrokenManimTests(unittest.TestCase):
    def _tmp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return d.name

    def test_locatable_but_broken_manim_is_failed(self):
        # A real external "manim" that fails to import, like a missing-pycairo install.
        broken = _write_executable(
            self._tmp(),
            "manim",
            f"#!{sys.executable}\n"
            "import sys\n"
            "sys.stderr.write('ImportError: No module named pycairo\\n')\n"
            "sys.exit(1)\n",
        )
        check = check_manim(frozen=True, which=lambda name: broken)  # real subprocess
        self.assertIs(check.status, CheckStatus.FAILED)
        self.assertIn("ImportError", check.detail)

    def test_external_manim_that_runs_is_ok_with_version(self):
        good = _write_executable(
            self._tmp(),
            "manim",
            f"#!{sys.executable}\nprint('Manim Community v0.18.1')\n",
        )
        check = check_manim(frozen=True, which=lambda name: good)
        self.assertIs(check.status, CheckStatus.OK)
        self.assertIn("0.18.1", check.detail)

    @unittest.skipIf(importlib.util.find_spec("manim") is None, "manim not installed")
    def test_real_manim_probe_on_this_interpreter_is_ok(self):
        # Integration: where manim is importable (the dev venv), the real
        # `-m manim --version` subprocess probe must succeed and parse a version.
        check = check_manim()
        self.assertIs(check.status, CheckStatus.OK)
        self.assertRegex(check.detail, r"\d+\.\d+")


if __name__ == "__main__":
    unittest.main()
