"""Tests for the `explainer plan` exit-code contract.

Every path here runs on FakeClient, so the whole file passes with no API key
and no network. Exit 2 must be reachable WITHOUT the client being called at
all -- that is the "cheap failures first" guarantee.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from explainer.cite.verifier import CiteFailure
from explainer.cli import MAX_BEATS_LIMIT, MAX_REPAIRS_LIMIT, main
from explainer.plan.client import ClientError, FakeClient, MaxTokensExhausted
from explainer.plan.critique import MAX_REVISIONS_LIMIT
from explainer.plan.emit import EmitError
from tests.explainer_tests.support import (
    confirm_write_is_blocked,
    evidence_reaching,
    git,
    make_git_repo,
)

SRC = "grounding_floor = 0.4\nmultiplier = 0.5\nfloor = max(a, grounding_floor)\n"


def _repo():
    return make_git_repo({"decay.py": SRC})


def _payload():
    beat = {
        "id": "floor",
        "canvas_kind": "code",
        "focus_index": 0,
        "cite_start": 1,
        "cite_end": 1,
        "display": "grounding_floor = 0.4",
        "narration": "A confirmed memory keeps a floor.",
        "caption": "",
        "caption_cite": None,
        "regions": [],
        "diagram": None,
    }
    second = dict(
        beat,
        id="mult",
        cite_start=2,
        cite_end=2,
        display="multiplier = 0.5",
        narration="A drifted one fades faster.",
    )
    return {"title": "T", "subtitle": "S", "beats": [beat, second]}


def _run(argv, client=None):
    """Run `explainer plan`, returning (exit_code, stdout_text, stderr_text).

    Capturing both streams (not just the exit code) is what lets every test
    below assert on message content, not only the numeric contract. BOTH
    provider constructors are patched to the same fake, so these tests hold
    for whichever provider is the default.
    """
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        if client is None:
            code = main(argv)
        else:
            with (
                patch("explainer.cli.AnthropicClient", return_value=client),
                patch("explainer.cli.GrokCliClient", return_value=client),
            ):
                code = main(argv)
    return code, out.getvalue(), err.getvalue()


def _argv(root, out, *extra):
    return ["plan", "--repo", root, "--focus", "decay.py", "--topic", "t", "-o", out, *extra]


class PlanExitCodeTests(unittest.TestCase):
    # -- E5: every case below asserts message content, not just the exit
    # code, so a regression that kept the right code but garbled the
    # message (or dropped it) would fail here. --------------------------

    def test_success_returns_0_and_writes_the_file(self):
        root = _repo()
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "s.story.yaml")
            code, stdout, _ = _run(_argv(root, out), FakeClient(_payload()))
            self.assertEqual(code, 0)
            self.assertTrue(os.path.exists(out))
        self.assertIn(f"planned: {out}", stdout)
        self.assertIn("next: explainer render", stdout)

    def test_bad_focus_returns_2_without_calling_the_client(self):
        root = _repo()
        client = FakeClient(_payload())
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "s.story.yaml")
            argv = ["plan", "--repo", root, "--focus", "nope.py", "--topic", "t", "-o", out]
            code, _, err = _run(argv, client)
            self.assertEqual(code, 2)
        self.assertEqual(client.calls, [])
        self.assertIn("nope.py", err)
        self.assertIn("cannot read focus target", err)

    def test_dirty_tree_returns_2(self):
        root = _repo()
        with open(os.path.join(root, "decay.py"), "a", encoding="utf-8") as handle:
            handle.write("dirty = 1\n")
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "s.story.yaml")
            code, _, err = _run(_argv(root, out), FakeClient(_payload()))
            self.assertEqual(code, 2)
        self.assertIn("uncommitted", err)
        self.assertIn("--allow-dirty", err)  # tells the user what to do about it

    def test_client_failure_returns_5(self):
        root = _repo()

        class _Boom:
            def draft(self, system, prompt, schema):
                raise ClientError("refused")

        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "s.story.yaml")
            code, _, err = _run(_argv(root, out), _Boom())
            self.assertEqual(code, 5)
        self.assertIn("refused", err)

    def test_max_tokens_exhausted_returns_6(self):
        # P6: max_tokens exhaustion is listed under exit 6 ("planner bug or
        # an unusable draft") in the design spec's exit ladder, not exit 5
        # ("LLM failure") -- it reached the model and made progress, unlike
        # an auth/transport/refusal failure.
        root = _repo()

        class _BudgetBoom:
            def draft(self, system, prompt, schema):
                raise MaxTokensExhausted("the model hit the max-tokens budget")

        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "s.story.yaml")
            code, _, err = _run(_argv(root, out), _BudgetBoom())
            self.assertEqual(code, 6)
        self.assertIn("max-tokens budget", err)

    def test_unusable_plan_returns_6(self):
        root = _repo()
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "s.story.yaml")
            bad = {"title": "T", "subtitle": "", "beats": []}
            code, _, err = _run(_argv(root, out), FakeClient(bad))
            self.assertEqual(code, 6)
        self.assertIn("no usable plan", err)
        self.assertIn("non-empty beats", err)

    def test_citation_failure_returns_3(self):
        root = _repo()
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "s.story.yaml")
            failure = [CiteFailure("floor", "decay.py:1", ("nope",))]
            with patch("explainer.plan.emit.verify_storyboard", return_value=failure):
                code, _, err = _run(_argv(root, out), FakeClient(_payload()))
                self.assertEqual(code, 3)
        self.assertIn("citation gate", err)
        self.assertIn("rejected draft kept at:", err)  # E4 positive control: real path prints

    # -- F1: a leftover ".rejected" from a failed run inside the cited repo
    # must not itself force every subsequent retry into needing
    # --allow-dirty (which would silently record allow_dirty: true on an
    # otherwise-clean tree, defeating the "verified @ commit" badge).

    def test_a_leftover_rejected_file_does_not_block_a_retry_in_the_same_repo(self):
        root = _repo()
        out = os.path.join(root, "s.story.yaml")  # -o INSIDE the cited repo
        failure = [CiteFailure("floor", "decay.py:1", ("nope",))]
        with patch("explainer.plan.emit.verify_storyboard", return_value=failure):
            code, _, _ = _run(_argv(root, out), FakeClient(_payload()))
            self.assertEqual(code, 3)
        self.assertTrue(os.path.exists(out + ".rejected"))
        self.assertFalse(os.path.exists(out))

        # Retry with the same repo, same -o, no --allow-dirty: the
        # underlying cause is gone (verify_storyboard is no longer patched),
        # but the ".rejected" file from the failed attempt above is still
        # sitting in the repo, untracked.
        code, stdout, err = _run(_argv(root, out), FakeClient(_payload()))
        self.assertEqual(code, 0)
        self.assertIn("planned:", stdout)
        self.assertTrue(os.path.exists(out))
        with open(out, encoding="utf-8") as handle:
            self.assertIn("allow_dirty: false", handle.read())

    def test_repair_attempt_is_made_before_giving_up(self):
        root = _repo()

        class _FlakyClient:
            def __init__(self):
                self.calls = []

            def draft(self, system, prompt, schema):
                self.calls.append((system, prompt))
                if len(self.calls) == 1:
                    return {"title": "T", "subtitle": "", "beats": []}
                return _payload()

        client = _FlakyClient()
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "s.story.yaml")
            code, stdout, _ = _run(_argv(root, out, "--revisions", "0"), client)
            self.assertEqual(code, 0)
        self.assertEqual(len(client.calls), 2)
        self.assertIn("rejected by validation", client.calls[1][1])
        self.assertIn("planned:", stdout)

    def test_dry_run_prints_the_prompt_and_never_calls_the_client(self):
        root = _repo()
        client = FakeClient(_payload())
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "s.story.yaml")
            code, stdout, stderr = _run(_argv(root, out, "--dry-run"), client)
            self.assertEqual(code, 0)
            self.assertFalse(os.path.exists(out))
        self.assertEqual(client.calls, [])
        self.assertIn("focus_index: 0", stdout)
        self.assertEqual(stderr, "")

    def test_plan_schema_is_json_serializable(self):
        from explainer.plan.plan_ir import PLAN_SCHEMA

        json.dumps(PLAN_SCHEMA)

    # -- E1: a non-UTF-8 focus file must exit 2, not crash ------------------

    def test_non_utf8_focus_file_returns_2_without_calling_the_client(self):
        root = _repo()
        bad = os.path.join(root, "bad.py")
        with open(bad, "wb") as handle:
            handle.write(b"value = 1  # not valid utf-8: \xff\xfe\n")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "add non-utf8 file")
        client = FakeClient(_payload())
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "s.story.yaml")
            argv = ["plan", "--repo", root, "--focus", "bad.py", "--topic", "t", "-o", out]
            code, _, err = _run(argv, client)
            self.assertEqual(code, 2)
        self.assertEqual(client.calls, [])
        self.assertIn("bad.py", err)
        self.assertIn("utf-8", err.lower())
        self.assertNotIn("Traceback", err)

    # -- E2: an unwritable output location must exit 2 (up front) or fail
    # closed as EmitError (late), never crash. -----------------------------

    def test_unwritable_output_directory_returns_2_without_constructing_client(self):
        root = _repo()
        directory = tempfile.mkdtemp()
        try:
            os.chmod(directory, 0o500)  # r-x: cannot create a file in here
            confirm_write_is_blocked(self, directory)
            out = os.path.join(directory, "s.story.yaml")
            # A stand-in whose __init__ raises is a stronger proof that the
            # client is never constructed than merely asserting .draft()
            # was not called: if _plan() reached this line at all, the
            # AssertionError below would escape main() and fail the test
            # with an error, not just a wrong return value.
            with (
                patch(
                    "explainer.cli.AnthropicClient",
                    side_effect=AssertionError("AnthropicClient must not be constructed"),
                ),
                patch(
                    "explainer.cli.GrokCliClient",
                    side_effect=AssertionError("GrokCliClient must not be constructed"),
                ),
            ):
                code, _, err = _run(_argv(root, out))
        finally:
            os.chmod(directory, 0o700)
        self.assertEqual(code, 2)
        self.assertIn(directory, err)
        self.assertNotIn("Traceback", err)

    def test_output_directory_becomes_unwritable_after_the_upfront_check(self):
        # The TOCTOU case E2 part 2 covers: validate_output_location() (the
        # up-front check) passes, but the real write_storyboard() call later
        # hits an unwritable directory anyway. Patching validate_output_location
        # to a no-op simulates "the check passed" without depending on a race
        # against the real filesystem.
        root = _repo()
        directory = tempfile.mkdtemp()
        try:
            with open(os.path.join(directory, "probe_setup"), "w", encoding="utf-8"):
                pass
            os.remove(os.path.join(directory, "probe_setup"))
            out = os.path.join(directory, "s.story.yaml")
            os.chmod(directory, 0o500)
            confirm_write_is_blocked(self, directory)
            with patch("explainer.cli.validate_output_location", return_value=None):
                code, _, err = _run(_argv(root, out), FakeClient(_payload()))
        finally:
            os.chmod(directory, 0o700)
        self.assertEqual(code, 6)  # EmitError kind "io" is not "citation" -> 6
        self.assertIn("cannot write storyboard", err)
        self.assertNotIn("Traceback", err)
        self.assertNotIn("rejected draft kept at", err)  # E4: rejected_path is None here

    # -- E3 / F6: --max-repairs must reject negative values and an unbounded
    # upper bound, and --beats must reject zero, negative, and unbounded
    # values, all before the client is ever constructed -- a plan with 0 (or
    # negative) beats contradicts the system prompt's own "at least one beat
    # MUST be a code beat" and would still render "Plan about 0 beats."
    # verbatim.

    def test_out_of_range_flag_values_return_2_without_calling_the_client(self):
        root = _repo()
        cases = [
            ("--max-repairs", "-1"),
            ("--max-repairs", str(MAX_REPAIRS_LIMIT + 1)),
            ("--beats", "0"),
            ("--beats", "-5"),
            ("--beats", str(MAX_BEATS_LIMIT + 1)),
            ("--revisions", "-1"),
            ("--revisions", str(MAX_REVISIONS_LIMIT + 1)),
        ]
        for flag, value in cases:
            with self.subTest(flag=flag, value=value):
                client = FakeClient(_payload())
                with tempfile.TemporaryDirectory() as directory:
                    out = os.path.join(directory, "s.story.yaml")
                    code, _, err = _run(_argv(root, out, flag, value), client)
                    self.assertEqual(code, 2)
                self.assertEqual(client.calls, [])
                self.assertIn(flag, err)
                self.assertIn(value, err)

    def test_boundary_flag_values_are_still_accepted(self):
        # Boundary checks: each limit itself is valid (only VALUES BEYOND it
        # are rejected) -- guards against an off-by-one in the comparison;
        # 1 is the smallest legal beat budget.
        root = _repo()
        cases = [
            ("--max-repairs", str(MAX_REPAIRS_LIMIT)),
            ("--beats", str(MAX_BEATS_LIMIT)),
            ("--beats", "1"),
            ("--revisions", str(MAX_REVISIONS_LIMIT)),
            ("--revisions", "0"),
        ]
        for flag, value in cases:
            with self.subTest(flag=flag, value=value):
                with tempfile.TemporaryDirectory() as directory:
                    out = os.path.join(directory, "s.story.yaml")
                    code, stdout, _ = _run(_argv(root, out, flag, value), FakeClient(_payload()))
                self.assertEqual(code, 0)
                self.assertIn("planned:", stdout)

    # -- E4: a None rejected_path must never print the word "None" ---------

    def test_emit_error_with_no_rejected_path_omits_the_rejected_line(self):
        root = _repo()
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "s.story.yaml")
            boom = EmitError("could not serialize at all", "schema", None)
            with patch("explainer.cli.write_storyboard", side_effect=boom):
                code, _, err = _run(_argv(root, out), FakeClient(_payload()))
        self.assertEqual(code, 6)
        self.assertIn("could not serialize at all", err)
        self.assertNotIn("None", err)
        self.assertNotIn("rejected draft kept at", err)

    def test_default_provider_is_grok(self):
        # The spec flips the default: a plan run with no --provider must
        # construct the grok client and must never touch the anthropic one.
        root = _repo()
        client = FakeClient(_payload())
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "s.story.yaml")
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                with (
                    patch("explainer.cli.GrokCliClient", return_value=client) as grok,
                    patch(
                        "explainer.cli.AnthropicClient",
                        side_effect=AssertionError("anthropic must not be the default"),
                    ),
                ):
                    code = main(_argv(root, out))
            self.assertEqual(code, 0)
            grok.assert_called_once()

    # -- Revision pass: one extra call by default, strictly never-worse ----

    def test_default_run_makes_a_draft_call_plus_one_revision_call(self):
        root = _repo()
        client = FakeClient(_payload())
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "s.story.yaml")
            code, _, _ = _run(_argv(root, out), client)
            self.assertEqual(code, 0)
        self.assertEqual(len(client.calls), 2)

    def test_revisions_zero_makes_exactly_one_call(self):
        root = _repo()
        client = FakeClient(_payload())
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "s.story.yaml")
            code, _, _ = _run(_argv(root, out, "--revisions", "0"), client)
            self.assertEqual(code, 0)
        self.assertEqual(len(client.calls), 1)

    def test_a_revision_that_never_parses_falls_back_to_the_draft(self):
        root = _repo()
        bad = {"title": "", "subtitle": "", "beats": []}
        client = FakeClient([_payload(), bad])  # draft good, revision bad forever
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "s.story.yaml")
            code, stdout, err = _run(_argv(root, out), client)
            self.assertEqual(code, 0)
            self.assertTrue(os.path.exists(out))
        self.assertIn("keeping the previous draft", err)
        self.assertIn("planned:", stdout)

    def test_a_revision_that_cannot_expand_falls_back_to_the_draft(self):
        # A diagram-only revision parses (plan_ir has no code-beat rule) but
        # fails expand()'s "no code beat survived" check -- the run must
        # still ship the original draft, exit 0.
        root = _repo()
        diagram_beat = {
            "id": "d1",
            "canvas_kind": "diagram",
            "focus_index": 0,
            "cite_start": 0,
            "cite_end": 0,
            "display": "",
            "narration": "Concept only.",
            "caption": "",
            "caption_cite": None,
            "regions": [],
            "diagram": {
                "nodes": [{"id": "a", "shape": "dot", "label": "", "color": "", "role": "middle"}],
                "edges": [],
            },
        }
        diagram_only = {
            "title": "T",
            "subtitle": "",
            "beats": [diagram_beat, dict(diagram_beat, id="d2")],
        }
        client = FakeClient([_payload(), diagram_only])
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "s.story.yaml")
            code, stdout, err = _run(_argv(root, out), client)
            self.assertEqual(code, 0)
            self.assertTrue(os.path.exists(out))
        self.assertIn("falling back to the original draft", err)
        self.assertIn("planned:", stdout)

    def test_a_revision_that_drops_a_beat_keeps_the_drafts_expansion(self):
        # Never-worse at beat granularity: the revision adds a third beat
        # citing a comment-only line, which drops in expand() -- the run
        # must ship the draft's expansion, not the thinner revision.
        src = SRC + "# just a comment line\n"
        root = make_git_repo({"decay.py": src})
        weak_beat = {
            "id": "weak-comment-beat",
            "canvas_kind": "code",
            "focus_index": 0,
            "cite_start": 4,
            "cite_end": 4,
            "display": "# just a comment line",
            "narration": "This beat cannot prove anything.",
            "caption": "",
            "caption_cite": None,
            "regions": [],
            "diagram": None,
        }
        revised = _payload()
        revised["beats"] = revised["beats"] + [weak_beat]
        client = FakeClient([_payload(), revised])
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "s.story.yaml")
            code, stdout, err = _run(_argv(root, out), client)
            self.assertEqual(code, 0)
            with open(out, encoding="utf-8") as handle:
                content = handle.read()
        self.assertIn("keeping the draft's expansion", err)
        self.assertNotIn("weak-comment-beat", content)
        self.assertIn("planned:", stdout)


def _comment_beat():
    """A beat citing line 4 of SRC + a comment line, which cannot back a claim."""
    return {
        "id": "weak-comment-beat",
        "canvas_kind": "code",
        "focus_index": 0,
        "cite_start": 4,
        "cite_end": 4,
        "display": "# just a comment line",
        "narration": "This beat cannot prove anything.",
        "caption": "",
        "caption_cite": None,
        "regions": [],
        "diagram": None,
    }


class CutLinesReportTests(unittest.TestCase):
    """A cite trimmed past a line of code is reported once, with the dropped
    beats, from the expansion that ships. evidence_reaching stands in for a
    selection whose token budget ran out early."""

    def _plan(self, client):
        root = make_git_repo({"decay.py": SRC + "# just a comment line\n"})
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "s.story.yaml")
            with evidence_reaching(1):
                code, _, err = _run(_argv(root, out), client)
        self.assertEqual(code, 0, err)
        return err

    def _with_cut_and_weak_beat(self):
        payload = _payload()
        payload["beats"][0].update(cite_start=1, cite_end=2)
        payload["beats"].append(_comment_beat())
        return payload

    def test_a_cut_and_a_dropped_beat_are_each_printed_once(self):
        # The revision repeats the draft, so it ships after the draft is
        # re-expanded to compare dropped beats: two expansions, one report.
        payload = self._with_cut_and_weak_beat()
        err = self._plan(FakeClient([payload, payload]))
        self.assertEqual(err.count("dropped beat 'weak-comment-beat': "), 1, err)
        self.assertEqual(err.count("dropped line 2 from a cite in beat 'floor': "), 1, err)
        self.assertIn("decay.py:1-2 is written as decay.py:1", err)

    def test_a_discarded_revisions_cut_is_not_printed(self):
        # The revision loses a beat the draft kept, so the draft's expansion
        # ships, and the revision's cut is not counted as a lost beat.
        err = self._plan(FakeClient([_payload(), self._with_cut_and_weak_beat()]))
        self.assertIn("the revision dropped 1 beat(s) where the draft dropped 0", err)
        self.assertNotIn("dropped line", err)
        self.assertNotIn("dropped beat", err)


if __name__ == "__main__":
    unittest.main()
