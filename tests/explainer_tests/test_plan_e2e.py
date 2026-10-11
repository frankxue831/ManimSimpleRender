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


def _plan_into(root, out):
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
            patch("explainer.cli.AnthropicClient", return_value=FakeClient(PAYLOAD)),
            patch("explainer.cli.GrokCliClient", return_value=FakeClient(PAYLOAD)),
        ):
            return main(argv)


def _verify(out):
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return main(["verify", out])


class PlannerEndToEndTests(unittest.TestCase):
    def test_generated_storyboard_passes_the_real_citation_gate(self):
        root = _repo()
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, "s.story.yaml")
            self.assertEqual(_plan_into(root, out), 0)
            self.assertEqual(_verify(out), 0)

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
