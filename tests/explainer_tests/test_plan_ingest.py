"""Tests for explainer.plan.ingest -- the deterministic half of the planner.

Ingest must fail on bad input BEFORE any API call is made, so every error
path here is a guard against paying for a request that was never going to
work. Focus specs are the planner's only source of source text.
"""

import os
import subprocess
import tempfile
import unittest

from explainer.plan.facts import FactSheet, FocusTarget
from explainer.plan.ingest import IngestError, build_factsheet, parse_focus
from tests.explainer_tests.support import make_git_repo as _repo

SRC = "line one\nline two\nline three\nline four\n"


class ParseFocusTests(unittest.TestCase):
    def test_bare_path_has_no_range(self):
        self.assertEqual(parse_focus("a/b.py"), ("a/b.py", None, None))

    def test_single_line(self):
        self.assertEqual(parse_focus("a/b.py:12"), ("a/b.py", 12, 12))

    def test_range(self):
        self.assertEqual(parse_focus("a/b.py:12-20"), ("a/b.py", 12, 20))

    def test_inverted_range_rejected(self):
        with self.assertRaises(IngestError):
            parse_focus("a/b.py:20-12")

    def test_zero_line_rejected(self):
        with self.assertRaises(IngestError):
            parse_focus("a/b.py:0")

    def test_non_numeric_range_rejected(self):
        with self.assertRaises(IngestError):
            parse_focus("a/b.py:abc")


class BuildFactSheetTests(unittest.TestCase):
    def test_bare_path_covers_whole_file(self):
        root = _repo({"src/a.py": SRC})
        sheet = build_factsheet(root, ["src/a.py"], "topic", 4, False)
        self.assertIsInstance(sheet, FactSheet)
        target = sheet.targets[0]
        self.assertIsInstance(target, FocusTarget)
        self.assertEqual((target.start, target.end), (1, 4))
        self.assertEqual(target.text, SRC.rstrip("\n"))

    def test_range_reads_exact_lines(self):
        root = _repo({"src/a.py": SRC})
        sheet = build_factsheet(root, ["src/a.py:2-3"], "topic", 4, False)
        self.assertEqual(sheet.targets[0].text, "line two\nline three")

    def test_missing_file_raises(self):
        root = _repo({"src/a.py": SRC})
        with self.assertRaises(IngestError):
            build_factsheet(root, ["src/nope.py"], "topic", 4, False)

    def test_range_past_end_of_file_raises(self):
        root = _repo({"src/a.py": SRC})
        with self.assertRaises(IngestError):
            build_factsheet(root, ["src/a.py:3-99"], "topic", 4, False)

    def test_commit_is_stamped_from_head(self):
        root = _repo({"src/a.py": SRC})
        sheet = build_factsheet(root, ["src/a.py"], "topic", 4, False)
        head = subprocess.run(
            ["git", "-C", root, "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        self.assertEqual(sheet.commit, head)
        self.assertFalse(sheet.dirty)

    def test_dirty_tree_rejected_without_allow_dirty(self):
        root = _repo({"src/a.py": SRC})
        with open(os.path.join(root, "src/a.py"), "a", encoding="utf-8") as handle:
            handle.write("dirty\n")
        with self.assertRaises(IngestError):
            build_factsheet(root, ["src/a.py"], "topic", 4, False)

    def test_dirty_tree_accepted_with_allow_dirty(self):
        root = _repo({"src/a.py": SRC})
        with open(os.path.join(root, "src/a.py"), "a", encoding="utf-8") as handle:
            handle.write("dirty\n")
        sheet = build_factsheet(root, ["src/a.py"], "topic", 4, True)
        self.assertTrue(sheet.dirty)

    def test_markdown_is_prose_kind(self):
        root = _repo({"docs/a.md": "some prose here\n"})
        sheet = build_factsheet(root, ["docs/a.md"], "topic", 4, False)
        self.assertEqual(sheet.targets[0].kind, "prose")

    def test_python_is_code_kind_with_language(self):
        root = _repo({"src/a.py": SRC})
        sheet = build_factsheet(root, ["src/a.py"], "topic", 4, False)
        self.assertEqual(sheet.targets[0].kind, "code")
        self.assertEqual(sheet.targets[0].language, "python")

    def test_absolute_focus_outside_repo_root_is_rejected(self):
        root = _repo({"src/a.py": SRC})
        outside_dir = tempfile.mkdtemp()
        secret = os.path.join(outside_dir, "secret.txt")
        with open(secret, "w", encoding="utf-8") as handle:
            handle.write("outside secret\n")
        with self.assertRaises(IngestError):
            build_factsheet(root, [secret], "topic", 4, False)

    def test_relative_traversal_outside_repo_root_is_rejected(self):
        root = _repo({"src/a.py": SRC})
        outside_dir = tempfile.mkdtemp()
        with open(os.path.join(outside_dir, "secret.txt"), "w", encoding="utf-8") as handle:
            handle.write("outside secret\n")
        traversal = os.path.join("..", os.path.basename(outside_dir), "secret.txt")
        with self.assertRaises(IngestError):
            build_factsheet(root, [traversal], "topic", 4, False)

    def test_absolute_focus_inside_repo_root_is_accepted(self):
        root = _repo({"src/a.py": SRC})
        absolute = os.path.join(root, "src/a.py")
        sheet = build_factsheet(root, [absolute], "topic", 4, False)
        self.assertEqual(sheet.targets[0].text, SRC.rstrip("\n"))

    def test_absolute_focus_inside_repo_root_is_recorded_relatively(self):
        # F3: facts.py's FocusTarget.path docstring promises "repo-relative,
        # spelled the way a cite spells it" -- an absolute --focus must not
        # leak the machine-specific path into the emitted cite (expand.py
        # builds the cite directly from this field via format_cite).
        root = _repo({"src/a.py": SRC})
        absolute = os.path.join(root, "src/a.py")
        sheet = build_factsheet(root, [absolute], "topic", 4, False)
        self.assertEqual(sheet.targets[0].path, os.path.join("src", "a.py"))
        self.assertFalse(os.path.isabs(sheet.targets[0].path))

    def test_relative_focus_path_is_unaffected(self):
        # The fix must not disturb the ordinary (already-relative) case.
        root = _repo({"src/a.py": SRC})
        sheet = build_factsheet(root, ["src/a.py"], "topic", 4, False)
        self.assertEqual(sheet.targets[0].path, "src/a.py")

    # -- F1: the planner's own in-flight temp files must not count as dirt --

    def test_ignore_paths_excludes_a_named_untracked_file_from_the_dirty_check(self):
        root = _repo({"src/a.py": SRC})
        stray = os.path.join(root, "out.story.yaml.rejected")
        with open(stray, "w", encoding="utf-8") as handle:
            handle.write("leftover from a previous failed run\n")
        sheet = build_factsheet(root, ["src/a.py"], "topic", 4, False, ignore_paths=(stray,))
        self.assertFalse(sheet.dirty)

    def test_ignore_paths_does_not_hide_a_genuine_edit(self):
        root = _repo({"src/a.py": SRC})
        stray = os.path.join(root, "out.story.yaml.rejected")
        with open(stray, "w", encoding="utf-8") as handle:
            handle.write("leftover from a previous failed run\n")
        with open(os.path.join(root, "src/a.py"), "a", encoding="utf-8") as handle:
            handle.write("dirty\n")
        with self.assertRaises(IngestError):
            build_factsheet(root, ["src/a.py"], "topic", 4, False, ignore_paths=(stray,))

    def test_ignore_paths_defaults_to_catching_everything(self):
        root = _repo({"src/a.py": SRC})
        with open(os.path.join(root, "out.story.yaml.rejected"), "w", encoding="utf-8") as handle:
            handle.write("leftover from a previous failed run\n")
        with self.assertRaises(IngestError):
            build_factsheet(root, ["src/a.py"], "topic", 4, False)

    def test_non_utf8_focus_file_raises_ingest_error_naming_the_file(self):
        # A focus file that is not valid UTF-8 (e.g. saved in another
        # encoding, or a stray binary) must fail closed as IngestError, not
        # escape as a raw UnicodeDecodeError -- see explainer/plan/ingest.py
        # _read_target's `open(full, encoding="utf-8")`. The bytes are
        # written directly here (never a committed binary fixture) so the
        # failure mode is genuinely reproduced rather than merely described.
        root = _repo({"src/a.py": SRC})
        bad = os.path.join(root, "src", "bad.py")
        with open(bad, "wb") as handle:
            handle.write(b"value = 1  # not valid utf-8: \xff\xfe\n")
        subprocess.run(["git", "-C", root, "add", "-A"], check=True)
        subprocess.run(["git", "-C", root, "commit", "-q", "-m", "add non-utf8 file"], check=True)
        with self.assertRaises(IngestError) as caught:
            build_factsheet(root, ["src/bad.py"], "topic", 4, False)
        message = str(caught.exception)
        self.assertIn("src/bad.py", message)
        self.assertIn("utf-8", message.lower())

    def test_symlink_escape_is_rejected(self):
        root = _repo({"src/a.py": SRC})
        outside_dir = tempfile.mkdtemp()
        secret = os.path.join(outside_dir, "secret.txt")
        with open(secret, "w", encoding="utf-8") as handle:
            handle.write("outside secret\n")
        link = os.path.join(root, "src", "link.txt")
        try:
            os.symlink(secret, link)
        except OSError as exc:
            self.skipTest(f"symlink creation unavailable on this platform: {exc}")
        # allow_dirty=True isolates the containment check: the symlink is an
        # untracked path, and the dirty-tree guard would otherwise raise its
        # own IngestError first and mask a regression in containment itself.
        with self.assertRaises(IngestError):
            build_factsheet(root, ["src/link.txt"], "topic", 4, True)


if __name__ == "__main__":
    unittest.main()
