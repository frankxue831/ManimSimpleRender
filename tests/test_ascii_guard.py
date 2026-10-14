"""ASCII guard for the app/ tree (BL-35).

CLAUDE.md/AGENTS.md forbid stray Unicode punctuation (em/en dashes, box drawing,
non-breaking hyphens) in code, comments, identifiers, and command strings. This
test fails on any non-ASCII byte in app/**/*.py so a regression is caught in CI.
On-screen Unicode belongs in rendered Text(...) content in examples/scenes/, not in app/.
Pure Python (no `grep -P`, which BSD/macOS grep lacks).
Run: python3 -m unittest tests.test_ascii_guard
"""

import glob
import os
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class AsciiGuardTests(unittest.TestCase):
    def test_app_tree_is_pure_ascii(self):
        offenders = []
        for path in glob.glob(os.path.join(_REPO_ROOT, "app", "**", "*.py"), recursive=True):
            with open(path, "rb") as handle:
                data = handle.read()
            try:
                data.decode("ascii")
            except UnicodeDecodeError as exc:
                line = data[: exc.start].count(b"\n") + 1
                offenders.append(f"{os.path.relpath(path, _REPO_ROOT)}:{line}")
        self.assertEqual(offenders, [], f"non-ASCII byte(s) in app/: {offenders}")


if __name__ == "__main__":
    unittest.main()
