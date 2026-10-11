"""Tests for explainer.cite.verifier -- the evidence-based citation gate.

The differentiator: every on-screen code fact cites real source lines and lists
evidence tokens that MUST be present there. A wrong constant or renamed symbol
fails the gate. Positive + negative cases are hermetic (temp source files); the
shipped example storyboard is verified against this repository's own
explainer/plan/evidence.py, so moving those lines means updating its cites.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from explainer.cite.verifier import verify_storyboard
from explainer.storyboard.model import (
    Beat,
    Canvas,
    Claim,
    CodeCallout,
    Diagram,
    DiaNode,
    Source,
    Storyboard,
    Theme,
    load_storyboard,
)

EXAMPLE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "examples",
    "evidence-budget.story.yaml",
)


def _storyboard(callout=None, claims=(), repo_root="/tmp"):
    beat = Beat(
        id="b",
        canvas=Canvas(kind="plot", spec={}),
        narration="n",
        code_callout=callout,
        caption="",
        claims=tuple(claims),
    )
    return Storyboard(
        version=1, title="t", source=Source(repo_root=repo_root), beats=(beat,), theme=Theme()
    )


class VerifierTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.src = os.path.join(self.dir, "x.rs")
        with open(self.src, "w", encoding="utf-8") as handle:
            handle.write("let a = 1;\nlet grounding_floor = 0.4;\nlet m = 0.5;\n")

    def _callout(self, cite, evidence):
        return CodeCallout(display="...", language="rust", cite=cite, evidence=tuple(evidence))

    def test_present_evidence_passes(self):
        sb = _storyboard(self._callout("x.rs:2", ["grounding_floor", "0.4"]), repo_root=self.dir)
        self.assertEqual(verify_storyboard(sb), [])

    def test_wrong_constant_fails_and_names_token(self):
        sb = _storyboard(self._callout("x.rs:2", ["0.7"]), repo_root=self.dir)
        failures = verify_storyboard(sb)
        self.assertEqual(len(failures), 1)
        self.assertIn("0.7", failures[0].missing)
        self.assertEqual(failures[0].cite, "x.rs:2")

    def test_evidence_on_wrong_line_fails(self):
        # 0.4 is on line 2, not line 1 -- citing line 1 must fail.
        sb = _storyboard(self._callout("x.rs:1", ["0.4"]), repo_root=self.dir)
        self.assertEqual(len(verify_storyboard(sb)), 1)

    def test_multiline_range_passes(self):
        sb = _storyboard(self._callout("x.rs:1-3", ["let a = 1", "0.5"]), repo_root=self.dir)
        self.assertEqual(verify_storyboard(sb), [])

    def test_out_of_range_cite_fails(self):
        sb = _storyboard(self._callout("x.rs:99", ["anything"]), repo_root=self.dir)
        self.assertEqual(len(verify_storyboard(sb)), 1)

    def test_missing_file_fails(self):
        sb = _storyboard(self._callout("nope.rs:1", ["x"]), repo_root=self.dir)
        self.assertEqual(len(verify_storyboard(sb)), 1)

    def test_regex_evidence_passes(self):
        sb = _storyboard(self._callout("x.rs:2", [r"/0\.\d+/"]), repo_root=self.dir)
        self.assertEqual(verify_storyboard(sb), [])

    def test_malformed_regex_evidence_does_not_crash(self):
        # ARCH-12: an unparseable /pattern/ token must FAIL the gate, never raise re.error.
        sb = _storyboard(self._callout("x.rs:2", ["/[/"]), repo_root=self.dir)
        failures = verify_storyboard(sb)  # must not raise
        self.assertEqual(len(failures), 1)
        self.assertIn("/[/", failures[0].missing)

    def test_whitespace_is_normalized_before_match(self):
        with open(self.src, "w", encoding="utf-8") as handle:
            handle.write("let floor = f32::max(access_floor,   grounding_floor);\n")
        sb = _storyboard(
            self._callout("x.rs:1", ["f32::max(access_floor, grounding_floor)"]), repo_root=self.dir
        )
        self.assertEqual(verify_storyboard(sb), [])

    def test_claims_are_verified(self):
        sb = _storyboard(claims=[Claim(cite="x.rs:3", evidence=("0.5",))], repo_root=self.dir)
        self.assertEqual(verify_storyboard(sb), [])

    def test_repo_root_override_takes_precedence(self):
        sb = _storyboard(self._callout("x.rs:2", ["0.4"]), repo_root="/does/not/exist")
        self.assertEqual(verify_storyboard(sb, repo_root=self.dir), [])


class ExampleStoryboardTests(unittest.TestCase):
    def test_example_storyboard_verifies_against_own_source(self):
        sb = load_storyboard(EXAMPLE)
        failures = verify_storyboard(sb)
        self.assertEqual(failures, [], f"unexpected citation failures: {failures}")

    def test_a_tampered_constant_would_fail(self):
        # Guard against a rubber-stamp gate: swap evidence to a wrong constant and
        # confirm the verifier rejects it.
        sb = load_storyboard(EXAMPLE)
        reveal = sb.beats[1]
        tampered = CodeCallout(
            display=reveal.code_callout.display,
            language=reveal.code_callout.language,
            cite=reveal.code_callout.cite,
            # a budget of 3 tokens per line is not what the source says
            evidence=("def _token_budget(line_count)", "max(BASE_TOKENS, 3 * line_count)"),
            regions=reveal.code_callout.regions,
        )
        bad_beat = Beat(
            id=reveal.id,
            canvas=reveal.canvas,
            narration=reveal.narration,
            code_callout=tampered,
            caption=reveal.caption,
            claims=(),
        )
        bad = Storyboard(
            version=sb.version,
            title=sb.title,
            source=sb.source,
            beats=(bad_beat,),
            theme=sb.theme,
        )
        failures = verify_storyboard(bad)
        self.assertEqual(len(failures), 1)
        self.assertIn("max(BASE_TOKENS, 3 * line_count)", failures[0].missing)


def _storyboard_for(repo_root, commit="", allow_dirty=False):
    """A storyboard whose one cite is satisfied, so only source-state can fail."""
    return Storyboard(
        version=1,
        title="T",
        source=Source(repo_root=repo_root, commit=commit, allow_dirty=allow_dirty),
        theme=Theme(),
        beats=(
            Beat(
                id="b1",
                canvas=Canvas(kind="code"),
                narration="hi",
                code_callout=CodeCallout(
                    display="x", language="text", cite="a.txt:1", evidence=("alpha",)
                ),
            ),
        ),
    )


@unittest.skipUnless(shutil.which("git"), "git not installed")
class SourceStateTests(unittest.TestCase):
    """The 'verified @ commit' badge must not overstate: when meta.source.commit
    is set, the repo's actual HEAD must match it, and a dirty tree fails unless
    allow_dirty. With no commit claimed, git is never consulted (back-compat,
    and hermetic temp-dir tests keep working)."""

    def _git_repo(self):
        root = tempfile.mkdtemp(prefix="explainer_src_")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)

        def git(*args):
            return subprocess.run(
                ["git", "-C", root, *args],
                check=True,
                capture_output=True,
                text=True,
                env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null"},
            )

        git("init", "-q")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "t")
        with open(os.path.join(root, "a.txt"), "w", encoding="utf-8") as fh:
            fh.write("alpha\n")
        git("add", "a.txt")
        git("commit", "-q", "-m", "init")
        return root, git("rev-parse", "HEAD").stdout.strip()

    def test_no_commit_claimed_skips_git_checks(self):
        with tempfile.TemporaryDirectory() as root:  # not a git repo at all
            with open(os.path.join(root, "a.txt"), "w", encoding="utf-8") as fh:
                fh.write("alpha\n")
            self.assertEqual(verify_storyboard(_storyboard_for(root)), [])

    def test_matching_commit_and_clean_tree_passes(self):
        root, head = self._git_repo()
        self.assertEqual(verify_storyboard(_storyboard_for(root, commit=head[:7])), [])

    def test_commit_mismatch_fails(self):
        root, _ = self._git_repo()
        failures = verify_storyboard(_storyboard_for(root, commit="deadbeef"))
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].reason, "commit_mismatch")
        self.assertIn("deadbeef", str(failures[0]))

    def test_dirty_tree_fails_without_allow_dirty(self):
        root, head = self._git_repo()
        with open(os.path.join(root, "b.txt"), "w", encoding="utf-8") as fh:
            fh.write("untracked\n")
        failures = verify_storyboard(_storyboard_for(root, commit=head[:7]))
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].reason, "dirty_tree")

    def test_dirty_tree_allowed_with_allow_dirty(self):
        root, head = self._git_repo()
        with open(os.path.join(root, "b.txt"), "w", encoding="utf-8") as fh:
            fh.write("untracked\n")
        sb = _storyboard_for(root, commit=head[:7], allow_dirty=True)
        self.assertEqual(verify_storyboard(sb), [])

    def test_commit_claimed_but_not_a_git_repo_fails(self):
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "a.txt"), "w", encoding="utf-8") as fh:
                fh.write("alpha\n")
            failures = verify_storyboard(_storyboard_for(root, commit="d3d7e09"))
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].reason, "git_unavailable")

    def test_git_binary_missing_fails_the_gate_instead_of_crashing(self):
        # A machine without git must produce a git_unavailable failure, not a
        # FileNotFoundError traceback out of subprocess.
        root, head = self._git_repo()
        with mock.patch(
            "explainer.cite.verifier.subprocess.run",
            side_effect=FileNotFoundError("No such file or directory: 'git'"),
        ):
            failures = verify_storyboard(_storyboard_for(root, commit=head[:7]))
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].reason, "git_unavailable")

    def test_too_short_or_nonhex_commit_prefix_is_rejected(self):
        # startswith() with a 1-char (or non-hex) claim is near-vacuous; the badge
        # must only carry claims that can actually fail. Git's own floor is 4.
        root, _ = self._git_repo()
        for bogus in ("2", "abc", "xyz1234"):
            failures = verify_storyboard(_storyboard_for(root, commit=bogus))
            self.assertEqual(len(failures), 1, bogus)
            self.assertEqual(failures[0].reason, "commit_invalid", bogus)

    # -- F1: the planner's own in-flight temp files must not count as dirt --
    #
    # emit.py stages a storyboard as "<output>.staging" (renamed to
    # ".rejected" on failure) inside the destination directory, which can be
    # the very repo the storyboard cites. Without an exclusion, writing a
    # storyboard into a clean repo would always trip dirty_tree on its own
    # scratch file.

    def test_ignored_untracked_file_does_not_count_as_dirty(self):
        root, head = self._git_repo()
        stray = os.path.join(root, "story.yaml.staging")
        with open(stray, "w", encoding="utf-8") as fh:
            fh.write("scratch\n")
        sb = _storyboard_for(root, commit=head[:7])
        self.assertEqual(verify_storyboard(sb, ignore_paths=(stray,)), [])

    def test_ignore_paths_does_not_hide_a_different_real_change(self):
        root, head = self._git_repo()
        stray = os.path.join(root, "story.yaml.staging")
        with open(stray, "w", encoding="utf-8") as fh:
            fh.write("scratch\n")
        with open(os.path.join(root, "b.txt"), "w", encoding="utf-8") as fh:
            fh.write("a genuine untracked edit\n")
        sb = _storyboard_for(root, commit=head[:7])
        failures = verify_storyboard(sb, ignore_paths=(stray,))
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].reason, "dirty_tree")

    def test_ignore_paths_defaults_to_catching_everything(self):
        # No ignore_paths given (the default) must behave exactly as before:
        # any untracked file, including one that happens to be named like a
        # planner scratch file, still fails the gate.
        root, head = self._git_repo()
        with open(os.path.join(root, "story.yaml.staging"), "w", encoding="utf-8") as fh:
            fh.write("scratch\n")
        sb = _storyboard_for(root, commit=head[:7])
        failures = verify_storyboard(sb)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].reason, "dirty_tree")


class DiagramLabelCiteTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        with open(os.path.join(self.dir, "x.rs"), "w", encoding="utf-8") as fh:
            fh.write("let grounding_floor = 0.4;\n")

    def _sb(self, evidence):
        node = DiaNode(
            id="n", shape="dot", at="center", label="floor", cite="x.rs:1", evidence=evidence
        )
        beat = Beat(
            id="b",
            canvas=Canvas(
                kind="diagram",
                diagram=Diagram(nodes=(node,), claims=(Claim(cite="x.rs:1", evidence=evidence),)),
            ),
            narration="n",
        )
        return Storyboard(
            version=1, title="t", source=Source(repo_root=self.dir), beats=(beat,), theme=Theme()
        )

    def test_cited_label_with_present_evidence_passes(self):
        self.assertEqual(verify_storyboard(self._sb(("grounding_floor", "0.4"))), [])

    def test_cited_label_with_wrong_evidence_fails(self):
        failures = verify_storyboard(self._sb(("0.9",)))
        self.assertEqual(len(failures), 1)
        self.assertIn("0.9", failures[0].missing)


if __name__ == "__main__":
    unittest.main()
