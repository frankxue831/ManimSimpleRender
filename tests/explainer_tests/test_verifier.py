"""Tests for explainer.cite.verifier -- the evidence-based citation gate.

The differentiator: every on-screen code fact cites real source lines and lists
evidence tokens that MUST be present there, as whole words, with evidence on the
first and last cited lines. Positive + negative cases are hermetic (temp source
files); the shipped example storyboards (examples/*.story.yaml) are verified
against this repository's own source, so an edit that moves cited evidence out
of its range, or off a range's first or last line, fails here.
"""

import glob
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from explainer.cite.verifier import match_evidence, verify_storyboard
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

    def test_regex_the_engine_cannot_compile_fails_instead_of_raising(self):
        # ARCH-12 beyond re.error: Python raises OverflowError for a repeat
        # count it cannot store, RecursionError for deeply nested groups and
        # ValueError for incompatible inline flags.
        for token in ("/a{99999999999}/", "/" + "(" * 1000 + ")" * 1000 + "/", "/(?a)(?u)x/"):
            with self.subTest(token=token[:16]):
                sb = _storyboard(self._callout("x.rs:2", [token]), repo_root=self.dir)
                failures = verify_storyboard(sb)  # must not raise
                self.assertEqual(len(failures), 1)
                self.assertIn(token, failures[0].missing)

    def test_cite_with_no_evidence_fails(self):
        # The loader refuses a cite without evidence; a storyboard built in
        # code must not get a verified badge for proving nothing either.
        sb = _storyboard(self._callout("x.rs:2", []), repo_root=self.dir)
        failures = verify_storyboard(sb)
        self.assertEqual([f.reason for f in failures], ["no_evidence"])

    def test_token_that_matches_any_text_is_missing(self):
        # The loader refuses these; the gate must not rely on it, because
        # storyboards are also built in code (the planner, these tests).
        for token in ("", "   ", "//", "/a*/"):
            with self.subTest(token=token):
                sb = _storyboard(
                    self._callout("x.rs:2", ["grounding_floor", token]), repo_root=self.dir
                )
                failures = verify_storyboard(sb)
                self.assertEqual(len(failures), 1)
                self.assertEqual(failures[0].missing, (token,))

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

    def test_every_shipped_storyboard_verifies_against_own_source(self):
        # Each examples/*.story.yaml cites this repo by line number. CI fails here when
        # an edit pushes an evidence token out of its cited range, or leaves a range's
        # first or last line without evidence.
        paths = sorted(glob.glob(os.path.join(os.path.dirname(EXAMPLE), "*.story.yaml")))
        self.assertIn(EXAMPLE, paths)
        for path in paths:
            with self.subTest(storyboard=os.path.basename(path)):
                failures = verify_storyboard(load_storyboard(path))
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

    def test_a_constant_that_gains_a_digit_would_fail(self):
        # The per-line beat cites TOKENS_PER_LINE = 2 with the evidence "2".
        # While plain tokens matched as substrings, changing the constant to 24
        # left the gate green, because "2" is inside "24".
        repo = os.path.dirname(os.path.dirname(EXAMPLE))
        cited = os.path.join("explainer", "plan", "evidence.py")
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root)
        with open(os.path.join(repo, cited), encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("TOKENS_PER_LINE = 2\n", source)
        os.makedirs(os.path.dirname(os.path.join(root, cited)))
        with open(os.path.join(root, cited), "w", encoding="utf-8") as handle:
            handle.write(source.replace("TOKENS_PER_LINE = 2\n", "TOKENS_PER_LINE = 24\n"))
        failures = verify_storyboard(load_storyboard(EXAMPLE), repo_root=root)
        self.assertEqual([(f.beat_id, f.missing) for f in failures], [("per-line", ("2",))])


class TightRangeTests(unittest.TestCase):
    """The first and last cited lines must each hold evidence.

    Without that, a cited range can drift: insert a line above the cited
    code and every token can still sit inside the old range, so the gate
    stays green while the on-screen file:start-end label is one line off.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        self._write("def budget(lines):\n    wanted = 2 * lines\n    return wanted\n")

    def _write(self, text):
        with open(os.path.join(self.root, "x.py"), "w", encoding="utf-8") as handle:
            handle.write(text)

    def _verify(self, cite, evidence):
        callout = CodeCallout(display="x", language="python", cite=cite, evidence=tuple(evidence))
        return verify_storyboard(_storyboard(callout, repo_root=self.root))

    def test_evidence_on_both_edge_lines_passes(self):
        self.assertEqual(self._verify("x.py:1-3", ["def budget(lines)", "return wanted"]), [])

    def test_edge_line_without_evidence_fails_and_names_the_tight_cite(self):
        for cite, evidence, tight in (
            ("x.py:1-3", ["wanted = 2 * lines", "return wanted"], "x.py:2-3"),
            ("x.py:1-3", ["def budget(lines)", "wanted = 2 * lines"], "x.py:1-2"),
        ):
            with self.subTest(evidence=evidence):
                failures = self._verify(cite, evidence)
                self.assertEqual([f.reason for f in failures], ["loose_range"])
                self.assertIn(tight, str(failures[0]))

    def test_regex_evidence_can_hold_an_edge_line(self):
        self.assertEqual(self._verify("x.py:1-3", ["def budget(lines)", r"/return \w+/"]), [])

    def test_leading_whitespace_in_a_regex_match_holds_no_line(self):
        # A match is placed by its visible characters. Otherwise "\s*" at the
        # front of a pattern reaches back onto the blank line above the code,
        # and that blank first line passes as holding evidence.
        self._write("\ndef budget(lines):\n    wanted = 2 * lines\n    return wanted\n")
        failures = self._verify("x.py:1-4", [r"/\s*def budget/", "return wanted"])
        self.assertEqual([f.reason for f in failures], ["loose_range"])

    def test_zero_width_regex_at_the_end_of_the_range_terminates(self):
        # pattern.search(text, pos) treats a pos past the end as the end, so a
        # loop over every match never finished on a zero-width match there.
        # A child process turns a regression into a timeout, not a hung suite.
        code = (
            "from explainer.cite.verifier import match_evidence\n"
            "print(match_evidence(['let a;'], ['/(?<=;)$/', r'/\\b/']).missing)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=os.path.dirname(os.path.dirname(EXAMPLE)),
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.stdout.strip(), "[]", result.stderr)

    def test_a_line_inserted_above_the_cited_code_fails(self):
        # "wanted" is on lines 2 and 3, so after the shift every token is
        # still inside 1-3; only the empty first line gives the drift away.
        evidence = ["def budget(lines)", "wanted"]
        self.assertEqual(self._verify("x.py:1-3", evidence), [])
        self._write("import math\ndef budget(lines):\n    wanted = 2 * lines\n    return wanted\n")
        failures = self._verify("x.py:1-3", evidence)
        self.assertEqual([f.reason for f in failures], ["loose_range"])


class MatchEvidenceTests(unittest.TestCase):
    def test_every_match_counts_toward_the_span_overlapping_ones_too(self):
        # "a a" occurs across lines 1-2 and, overlapping that, across lines
        # 2-3; only the second reaches the last line.
        self.assertEqual(match_evidence(["b a", "a", "a"], ["a a"]).span, (0, 2))


class WholeWordTests(unittest.TestCase):
    """A plain token is found only as whole words, never inside a longer word.

    Word characters are ASCII letters, digits and underscore. Text in other
    scripts keeps substring matching, since CJK has no spaces between words.
    """

    CASES = (
        # (cited lines, token, found)
        (["TOKENS_PER_LINE = 24"], "2", False),
        (["TOKENS_PER_LINE = 2"], "2", True),
        (["width = 24; height = 2"], "2", True),
        (["MAX_TOKENS = 24"], "TOKENS", False),
        (["let grounding_floor_v2 = 0.4;"], "grounding_floor", False),
        (["self.grounding_floor = x"], "grounding_floor", True),
        (["access = 0.5_f64.powf(days)"], "0.5", False),
        (["access = 0.5_f64.powf(days)"], "0.5_f64", True),
        (["access = 0.5_f64.powf(days)"], "powf(", True),
        (['fail("dirty_tree", detail)'], '"dirty_tree"', True),
        (["if failures:", "    raise GateError(failures)"], "failures: raise GateError", True),
        (["if failures:", "", "    raise GateError(f)"], "failures: raise GateError", True),
        (["x = self.value"], "self.", True),
        (["def f():", "    return x"], r"/(?m)^def f\(\):\n    return x$/", True),
        (["MAX_TOKENS = 24"], "/TOKENS/", True),
        (["\u8fd9\u662f\u5f15\u7528\u95e8\u7684\u8bf4\u660e"], "\u5f15\u7528\u95e8", True),
        (["\u8c03\u7528API\u63a5\u53e3"], "API", True),
    )

    def test_plain_tokens_match_whole_words(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root)
        for lines, token, found in self.CASES:
            with self.subTest(lines=lines, token=token):
                with open(os.path.join(root, "x.py"), "w", encoding="utf-8") as handle:
                    handle.write("\n".join(lines) + "\n")
                cite = "x.py:1" if len(lines) == 1 else f"x.py:1-{len(lines)}"
                callout = CodeCallout(display="x", language="python", cite=cite, evidence=(token,))
                failures = verify_storyboard(_storyboard(callout, repo_root=root))
                if found:
                    self.assertEqual(failures, [])
                else:
                    self.assertEqual([f.missing for f in failures], [(token,)])


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
