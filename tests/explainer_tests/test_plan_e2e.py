"""End-to-end planner tests, and the drift test that proves the guarantee.

No API key and no network: FakeClient supplies the plan IR, and every other
stage is the real one -- ingest, evidence extraction, expansion, the real
load_storyboard, and the real citation gate.
"""

import copy
import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from explainer.cli import main
from explainer.plan.client import FakeClient
from explainer.storyboard.model import load_storyboard
from tests.explainer_tests.support import make_git_repo

SRC = """\
def access_factor(days_since_access):
    return 0.5 ** (days_since_access / 30.0)


def grounding_floor(status):
    return 0.4 if status == "confirmed" else 0.0
"""


def _repo():
    return make_git_repo({"decay.py": SRC})


PAYLOAD = {
    "title": "How memory decay works",
    "subtitle": "A tiny model of forgetting",
    "beats": [
        {
            "id": "half-life",
            "canvas_kind": "code",
            "focus_index": 0,
            "cite_start": 2,
            "cite_end": 2,
            "display": "return 0.5 ** (days_since_access / 30.0)",
            "narration": "Importance halves every thirty days.",
            "caption": "",
            "caption_cite": None,
            "regions": [{"lines": [1], "narration": "A thirty day half life."}],
            "diagram": None,
        },
        {
            "id": "the-floor",
            "canvas_kind": "code",
            "focus_index": 0,
            "cite_start": 6,
            "cite_end": 6,
            "display": 'return 0.4 if status == "confirmed" else 0.0',
            "narration": "A confirmed memory cannot fall below a floor.",
            "caption": "",
            "caption_cite": None,
            "regions": [],
            "diagram": None,
        },
    ],
}


def _plan(root, out, payload=PAYLOAD, focus="decay.py"):
    """Run `explainer plan` on a FakeClient; return ``(exit code, stderr)``."""
    argv = [
        "plan",
        "--repo",
        root,
        "--focus",
        focus,
        "--topic",
        "how decay works",
        "-o",
        out,
    ]
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        with (
            patch("explainer.cli.AnthropicClient", return_value=FakeClient(payload)),
            patch("explainer.cli.GrokCliClient", return_value=FakeClient(payload)),
        ):
            return main(argv), err.getvalue()


def _plan_into(root, out, payload=PAYLOAD):
    return _plan(root, out, payload)[0]


def _verify_reporting(out):
    """Run the real `explainer verify`; return ``(exit code, stderr)``."""
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        return main(["verify", out]), err.getvalue()


def _verify(out):
    return _verify_reporting(out)[0]


class PlannerEndToEndTests(unittest.TestCase):
    def test_generated_storyboard_passes_the_real_citation_gate(self):
        root = _repo()
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "s.story.yaml")
            self.assertEqual(_plan_into(root, out), 0)
            self.assertEqual(_verify(out), 0)

    def test_a_cite_with_blank_edge_lines_is_narrowed_and_still_passes(self):
        # A model cites whole blocks, blank lines around them included. The
        # gate fails a cite whose first or last line holds no evidence, so the
        # planner must narrow each cite, at either end, on the way out.
        payload = copy.deepcopy(PAYLOAD)
        payload["beats"][0].update(cite_start=1, cite_end=3)
        payload["beats"][1].update(cite_start=4, cite_end=6)
        root = _repo()
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "s.story.yaml")
            self.assertEqual(_plan_into(root, out, payload), 0)
            self.assertEqual(_verify(out), 0)
            cites = [beat.code_callout.cite for beat in load_storyboard(out).beats]
        self.assertEqual(cites, ["decay.py:1-2", "decay.py:5-6"])

    def test_generated_evidence_reflects_the_real_source(self):
        root = _repo()
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "s.story.yaml")
            self.assertEqual(_plan_into(root, out), 0)
            with open(out, encoding="utf-8") as handle:
                text = handle.read()
        self.assertIn("30.0", text)
        self.assertIn("0.4", text)

    def test_source_drift_makes_a_previously_passing_storyboard_fail(self):
        # THE test. Generate against real source, then change a cited constant
        # and confirm the gate now rejects the storyboard. If this ever passes
        # silently, the citation guarantee is decorative.
        root = _repo()
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "s.story.yaml")
            self.assertEqual(_plan_into(root, out), 0)
            self.assertEqual(_verify(out), 0)

            drifted = SRC.replace("30.0", "45.0").replace("0.4 if", "0.7 if")
            with open(os.path.join(root, "decay.py"), "w", encoding="utf-8") as handle:
                handle.write(drifted)

            self.assertEqual(_verify(out), 3)


# Twelve constants put two strong tokens on each of their lines, which fills
# the 24-token budget, and the function under them holds only weak ones; a
# blank line ends the file. The reviewer's example on pull request 8, where
# the planner wrote a cite of lines 1-20 as 1-12 and said nothing.
LONG_SRC = (
    "\n".join(
        [f"LIMIT_{i} = {i + 10}" for i in range(12)]
        + [
            "def scale(values, factor):",
            "    running = []",
            "    for value in values:",
            "        running += [value * factor]",
            "    total = sum(running)",
            "    if total > factor:",
            "        total = factor",
            "    return round(total)",
            "",
        ]
    )
    + "\n"
)

LONG_PAYLOAD = {
    "title": "Capped totals",
    "subtitle": "Limits and the function that applies them",
    "beats": [
        {
            "id": "scale",
            "canvas_kind": "code",
            "focus_index": 0,
            "cite_start": 1,
            "cite_end": 21,
            "display": "def scale(values, factor): ... return round(total)",
            "narration": "Twelve limits, then the function that caps a scaled total.",
            "caption": "",
            "caption_cite": None,
            "regions": [],
            "diagram": None,
        },
        {
            "id": "first-limit",
            "canvas_kind": "code",
            "focus_index": 0,
            "cite_start": 1,
            "cite_end": 1,
            "display": "LIMIT_0 = 10",
            "narration": "The first limit is ten.",
            "caption": "",
            "caption_cite": None,
            "regions": [],
            "diagram": None,
        },
    ],
}


class LongCiteTests(unittest.TestCase):
    def test_a_long_cite_keeps_its_code_and_the_gate_guards_its_last_line(self):
        root = make_git_repo({"limits.py": LONG_SRC})
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "s.story.yaml")
            code, err = _plan(root, out, LONG_PAYLOAD, focus="limits.py")
            self.assertEqual(code, 0, err)
            self.assertEqual(_verify_reporting(out), (0, ""))
            cites = [beat.code_callout.cite for beat in load_storyboard(out).beats]
            # Only the blank line 21, which cannot hold evidence, is trimmed.
            self.assertEqual(cites, ["limits.py:1-20", "limits.py:1"])
            self.assertNotIn("dropped", err)

            drifted = LONG_SRC.replace("return round(total)", "return int(total)")
            with open(os.path.join(root, "limits.py"), "w", encoding="utf-8") as handle:
                handle.write(drifted)
            code, err = _verify_reporting(out)
        self.assertEqual(code, 3)
        self.assertIn("[scale] limits.py:1-20: evidence_not_found", err)

    def test_a_cut_line_of_code_is_reported_and_the_rest_verifies(self):
        # "Ok" is too short to be a token, so no evidence can reach Ok(()):
        # the trim cuts it with the closing brace and says so, once.
        root = make_git_repo({"check.rs": CHECK_SRC})
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "s.story.yaml")
            code, err = _plan(root, out, CHECK_PAYLOAD, focus="check.rs")
            self.assertEqual(code, 0, err)
            self.assertEqual(_verify_reporting(out), (0, ""))
            cites = [beat.code_callout.cite for beat in load_storyboard(out).beats]
        self.assertEqual(cites, ["check.rs:1-2", "check.rs:2"])
        self.assertEqual(err.count("dropped line 3 from a cite in beat 'check': "), 1, err)
        self.assertIn("check.rs:1-4 is written as check.rs:1-2", err)


CHECK_SRC = "fn check(&self) -> Result<(), Error> {\n    validate(&self.config)?;\n    Ok(())\n}\n"

CHECK_PAYLOAD = {
    "title": "Checked config",
    "subtitle": "A function that validates before it succeeds",
    "beats": [
        {
            "id": "check",
            "canvas_kind": "code",
            "focus_index": 0,
            "cite_start": 1,
            "cite_end": 4,
            "display": "fn check(&self) -> Result<(), Error> { ... Ok(()) }",
            "narration": "The check validates the config, then succeeds.",
            "caption": "",
            "caption_cite": None,
            "regions": [],
            "diagram": None,
        },
        {
            "id": "validate",
            "canvas_kind": "code",
            "focus_index": 0,
            "cite_start": 2,
            "cite_end": 2,
            "display": "validate(&self.config)?;",
            "narration": "A failed validation returns early.",
            "caption": "",
            "caption_cite": None,
            "regions": [],
            "diagram": None,
        },
    ],
}


class PlanOutputInsideTheCitedRepoTests(unittest.TestCase):
    """F1: writing the storyboard inside the repo it explains must work on a
    clean tree, and must still catch a genuinely dirty one -- the fix must
    not launder real uncommitted edits, only its own scratch files.
    """

    def test_clean_repo_with_output_inside_it_succeeds(self):
        # Scope note: this only pins what F1 asks for -- the run itself must
        # succeed and must not leave scratch files behind. It deliberately
        # does NOT assert that a later, separate `explainer verify` also
        # stays green: writing story.yaml INTO the repo makes it genuinely
        # untracked, and that real (if mundane) change is exactly the kind of
        # dirt the gate exists to catch on a later, independent check -- only
        # the planner's OWN in-flight .staging/.rejected are exempt.
        root = _repo()
        out = os.path.join(root, "story.yaml")
        self.assertEqual(_plan_into(root, out), 0)
        self.assertTrue(os.path.exists(out))
        self.assertFalse(os.path.exists(out + ".staging"))
        self.assertFalse(os.path.exists(out + ".rejected"))
        storyboard = load_storyboard(out)
        self.assertFalse(storyboard.source.allow_dirty)

    def test_genuine_uncommitted_edit_is_still_caught(self):
        root = _repo()
        with open(os.path.join(root, "decay.py"), "a", encoding="utf-8") as handle:
            handle.write("# a genuine uncommitted edit\n")
        out = os.path.join(root, "story.yaml")
        self.assertEqual(_plan_into(root, out), 2)
        self.assertFalse(os.path.exists(out))


class RevisionShipsTests(unittest.TestCase):
    def test_the_revised_plan_is_what_ships_and_it_passes_the_gate(self):
        revised = copy.deepcopy(PAYLOAD)
        revised["beats"][0]["narration"] = (
            "The revised argument: importance halves every thirty days, "
            "so silence is a slow delete."
        )
        client = FakeClient([PAYLOAD, revised])
        root = _repo()
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "s.story.yaml")
            argv = [
                "plan",
                "--repo",
                root,
                "--focus",
                "decay.py",
                "--topic",
                "how decay works",
                "-o",
                out,
            ]
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                with (
                    patch("explainer.cli.AnthropicClient", return_value=client),
                    patch("explainer.cli.GrokCliClient", return_value=client),
                ):
                    self.assertEqual(main(argv), 0)
            with open(out, encoding="utf-8") as handle:
                content = handle.read()
            # Whitespace-normalized: YAML folds long scalars at width 88, and the
            # assertion must not depend on where the fold lands.
            self.assertIn("silence is a slow delete", " ".join(content.split()))
            self.assertEqual(_verify(out), 0)


if __name__ == "__main__":
    unittest.main()
