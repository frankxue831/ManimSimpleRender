"""Tests for explainer.plan.expand.

Expansion is where the model's judgement becomes a storyboard: it never
supplies evidence or anchors, so this module must. The decisive assertion is
that its output loads through the real load_storyboard.
"""

import os
import tempfile
import unittest

import yaml

from explainer.plan.expand import DroppedBeat, ExpandError, expand
from explainer.plan.facts import FactSheet, FocusTarget
from explainer.plan.plan_ir import parse
from explainer.storyboard.model import load_storyboard

TARGET = FocusTarget(
    path="decay.rs",
    start=90,
    end=93,
    text=(
        "let grounding_floor = 0.4;\n"
        "let multiplier = 0.5;\n"
        "let floor = f32::max(access_floor, grounding_floor);\n"
        "}"
    ),
    language="rust",
    kind="code",
)
SHEET = FactSheet(
    repo_root="/tmp/some/repo",
    commit="abc1234",
    dirty=False,
    topic="t",
    targets=(TARGET,),
    beat_budget=2,
)


def _code_beat(**overrides):
    beat = {
        "id": "the-floor",
        "canvas_kind": "code",
        "focus_index": 0,
        "cite_start": 90,
        "cite_end": 90,
        "display": "let grounding_floor = 0.4;",
        "narration": "A confirmed memory gets a floor.",
        "caption": "",
        "caption_cite": None,
        "regions": [{"lines": [1], "narration": "The floor is a constant."}],
        "diagram": None,
    }
    beat.update(overrides)
    return beat


def _diagram_beat(**overrides):
    beat = {
        "id": "the-field",
        "canvas_kind": "diagram",
        "focus_index": 0,
        "cite_start": 0,
        "cite_end": 0,
        "display": "",
        "narration": "An agent crosses a field.",
        "caption": "",
        "caption_cite": None,
        "regions": [],
        "diagram": {
            "nodes": [
                {"id": "agent", "shape": "dot", "label": "agent", "color": "", "role": "source"},
                {"id": "goal", "shape": "ring", "label": "goal", "color": "gold", "role": "sink"},
            ],
            "edges": [
                {
                    "kind": "arrow",
                    "from": "agent",
                    "to": "goal",
                    "label": "action",
                    "narration": "",
                    "color": "blue",
                },
            ],
        },
    }
    beat.update(overrides)
    return beat


def _plan(beats):
    return parse({"title": "T", "subtitle": "S", "beats": beats}, SHEET)


def _expanded(beats, output_dir="/tmp/out"):
    return expand(_plan(beats), SHEET, output_dir)


class ExpandTests(unittest.TestCase):
    def test_code_beat_gets_evidence_extracted_from_source(self):
        # A companion beat pads past MIN_BEATS; this test's own assertions
        # are about the first beat only.
        doc, dropped = _expanded([_code_beat(), _code_beat(id="companion")])
        self.assertEqual(dropped, ())
        callout = doc["beats"][0]["code_callout"]
        self.assertEqual(callout["cite"], "decay.rs:90")
        self.assertIn("0.4", callout["evidence"])

    def test_diagram_beat_gets_anchors_and_no_callout(self):
        doc, _ = _expanded([_diagram_beat(), _code_beat()])
        beat = doc["beats"][0]
        self.assertNotIn("code_callout", beat)
        anchors = {n["id"]: n["at"] for n in beat["canvas"]["spec"]["nodes"]}
        # agent->goal is a 2-node chain, placed center-aligned on the horizontal band
        self.assertEqual(anchors["agent"], "center-left")
        self.assertEqual(anchors["goal"], "center")

    def test_edges_are_written_in_the_node_endpoint_form(self):
        doc, _ = _expanded([_diagram_beat(), _code_beat()])
        edge = doc["beats"][0]["canvas"]["spec"]["edges"][0]
        self.assertEqual(edge["from"], {"node": "agent"})
        self.assertEqual(edge["to"], {"node": "goal"})

    def test_caption_cite_becomes_a_verified_claim(self):
        beat = _code_beat(
            caption="the floor is 0.4", caption_cite={"focus_index": 0, "start": 90, "end": 90}
        )
        # A companion beat pads past MIN_BEATS; this test's own assertions
        # are about the first beat only.
        doc, _ = _expanded([beat, _code_beat(id="companion")])
        claim = doc["beats"][0]["claims"][0]
        self.assertEqual(claim["cite"], "decay.rs:90")
        self.assertTrue(claim["evidence"])

    def test_diagram_caption_cite_becomes_a_verified_claim(self):
        # Same generalization as the code-beat version above: a diagram
        # beat's caption_cite is just as verifiable as a code beat's own
        # cite (design spec section 7 states this with no code-only
        # qualifier).
        beat = _diagram_beat(
            caption="the floor is 0.4",
            caption_cite={"focus_index": 0, "start": 90, "end": 90},
        )
        doc, dropped = _expanded([beat, _code_beat()])
        self.assertEqual(dropped, ())
        claim = doc["beats"][0]["claims"][0]
        self.assertEqual(claim["cite"], "decay.rs:90")
        self.assertTrue(claim["evidence"])

    def test_weak_cite_drops_the_beat_instead_of_weakening_it(self):
        # A second surviving companion keeps the survivor count (the-floor,
        # companion) at MIN_BEATS after "weak" is dropped; without it this
        # fixture is exactly the below-floor case
        # test_fewer_than_two_surviving_beats_is_an_error pins below.
        weak = _code_beat(id="weak", cite_start=93, cite_end=93, display="}")
        doc, dropped = _expanded([weak, _code_beat(), _code_beat(id="companion")])
        self.assertEqual([d.beat_id for d in dropped], ["weak"])
        self.assertIsInstance(dropped[0], DroppedBeat)
        self.assertEqual(len(doc["beats"]), 2)
        self.assertNotIn("weak", [b["id"] for b in doc["beats"]])

    def test_weak_diagram_caption_cite_drops_the_whole_beat(self):
        # A diagram beat's caption implies a citation exactly like a code
        # beat's does; a caption_cite too weak to back it drops the entire
        # beat (topology included), not just the caption.
        weak = _diagram_beat(
            id="weak-field",
            caption="just a closing brace",
            caption_cite={"focus_index": 0, "start": 93, "end": 93},
        )
        doc, dropped = _expanded([weak, _code_beat(), _code_beat(id="companion")])
        self.assertEqual([d.beat_id for d in dropped], ["weak-field"])
        self.assertNotIn("weak-field", [b["id"] for b in doc["beats"]])
        self.assertEqual(len(doc["beats"]), 2)

    def test_repo_root_is_relative_to_the_output_directory(self):
        # A companion beat pads past MIN_BEATS; irrelevant to meta.source.
        doc, _ = _expanded(
            [_code_beat(), _code_beat(id="companion")], output_dir="/tmp/some/stories"
        )
        self.assertEqual(doc["meta"]["source"]["repo_root"], "../repo")

    def test_commit_and_allow_dirty_are_stamped(self):
        # A companion beat pads past MIN_BEATS; irrelevant to meta.source.
        doc, _ = _expanded([_code_beat(), _code_beat(id="companion")])
        self.assertEqual(doc["meta"]["source"]["commit"], "abc1234")
        self.assertFalse(doc["meta"]["source"]["allow_dirty"])

    def test_unicode_punctuation_is_transliterated(self):
        beat = _code_beat(narration="a \u2014 b \u201cq\u201d")
        # A companion beat pads past MIN_BEATS; this test's own assertions
        # are about the first beat only.
        doc, _ = _expanded([beat, _code_beat(id="companion")])
        narration = doc["beats"][0]["narration"]
        narration.encode("ascii")
        self.assertIn("--", narration)

    def test_untransliterable_non_ascii_is_an_error(self):
        with self.assertRaises(ExpandError):
            _expanded([_code_beat(narration="decay \u4e2d\u6587")])

    def test_all_beats_dropped_is_an_error(self):
        with self.assertRaises(ExpandError):
            _expanded([_code_beat(id="w1", cite_start=93, cite_end=93, display="}")])

    def test_fewer_than_two_surviving_beats_is_an_error(self):
        # Pins the 2-beat survival floor itself: one beat surviving is not
        # "nothing survived" (test_all_beats_dropped_is_an_error, above) but
        # still leaves no arc, so it must raise the same way.
        weak = _code_beat(id="weak", cite_start=93, cite_end=93, display="}")
        with self.assertRaises(ExpandError) as ctx:
            _expanded([weak, _code_beat()])
        message = str(ctx.exception).lower()
        self.assertIn("1", message)
        self.assertIn("survive", message)

    def test_output_loads_through_the_real_storyboard_loader(self):
        # The decisive test: expansion must produce something the shipped
        # loader accepts, not merely something that looks right.
        doc, _ = _expanded([_diagram_beat(), _code_beat()])
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "s.story.yaml")
            with open(path, "w", encoding="utf-8") as handle:
                yaml.safe_dump(doc, handle, sort_keys=False, allow_unicode=False)
            storyboard = load_storyboard(path)
        self.assertEqual(len(storyboard.beats), 2)
        self.assertEqual(storyboard.beats[0].canvas.kind, "diagram")


if __name__ == "__main__":
    unittest.main()
