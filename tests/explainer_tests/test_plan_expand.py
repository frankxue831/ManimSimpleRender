"""Tests for explainer.plan.expand.

Expansion is where the model's judgement becomes a storyboard: it never
supplies evidence or anchors, so this module must. The decisive assertion is
that its output loads through the real load_storyboard.
"""

import os
import tempfile
import unittest

import yaml

from explainer.plan.expand import DroppedBeat, DroppedLines, ExpandError, expand
from explainer.plan.facts import FactSheet, FocusTarget
from explainer.plan.plan_ir import parse
from explainer.storyboard.model import load_storyboard
from tests.explainer_tests.support import evidence_reaching

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

    def test_cites_are_trimmed_to_the_lines_their_evidence_holds(self):
        # The citation gate fails a cite whose first or last line holds no
        # evidence. A model that cites a whole block ends on its closing
        # brace, which no extracted token can hold, so expansion narrows both
        # kinds of cite (a code beat's own and a caption's claim) to the lines
        # the evidence covers.
        beat = _code_beat(
            cite_start=90,
            cite_end=93,
            caption="the floor is 0.4",
            caption_cite={"focus_index": 0, "start": 90, "end": 93},
        )
        doc, dropped = _expanded([beat, _code_beat(id="companion")])
        self.assertEqual(dropped, ())
        self.assertEqual(doc["beats"][0]["code_callout"]["cite"], "decay.rs:90-92")
        self.assertEqual(doc["beats"][0]["claims"][0]["cite"], "decay.rs:90-92")

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


CHECK = FocusTarget(
    path="check.rs",
    start=1,
    end=4,
    text="fn check(&self) -> Result<(), Error> {\n    validate(&self.config)?;\n    Ok(())\n}",
    language="rust",
    kind="code",
)


class CutLinesReportTests(unittest.TestCase):
    """A trim that cuts a line of code or text is reported, not silent.

    The cite is still trimmed, since the gate fails an edge line without
    evidence, but its label then covers less than the display shows. The
    report goes out with the dropped beats, the channel the CLI already
    prints. evidence_reaching stands in for a selection whose budget ran out.
    """

    def _cuts(self, dropped):
        return [(d.beat_id, d.cite, d.kept, d.lines) for d in dropped]

    def test_a_cut_code_line_is_reported_with_the_dropped_beats(self):
        beat = _code_beat(id="long", cite_start=90, cite_end=91)
        with evidence_reaching(1):
            doc, dropped = _expanded([beat, _code_beat(id="companion")])
        self.assertEqual(doc["beats"][0]["code_callout"]["cite"], "decay.rs:90")
        self.assertEqual(self._cuts(dropped), [("long", "decay.rs:90-91", "decay.rs:90", (91,))])

    def test_a_cut_at_the_top_of_a_cite_is_reported(self):
        beat = _code_beat(id="long", cite_start=90, cite_end=91)
        with evidence_reaching(1, from_end=True):
            doc, dropped = _expanded([beat, _code_beat(id="companion")])
        self.assertEqual(doc["beats"][0]["code_callout"]["cite"], "decay.rs:91")
        self.assertEqual(self._cuts(dropped), [("long", "decay.rs:90-91", "decay.rs:91", (90,))])

    def test_a_cut_line_of_code_without_a_candidate_is_reported(self):
        # No selection can reach Ok(()): "Ok" is too short to be a token. The
        # trim cuts it with the closing brace, and only line 3 holds code.
        sheet = FactSheet(
            repo_root="/tmp/some/repo",
            commit="abc1234",
            dirty=False,
            topic="t",
            targets=(TARGET, CHECK),
            beat_budget=2,
        )
        beat = _code_beat(id="check", focus_index=1, cite_start=1, cite_end=4)
        plan = parse({"title": "T", "subtitle": "S", "beats": [beat, _code_beat()]}, sheet)
        doc, dropped = expand(plan, sheet, "/tmp/out")
        self.assertEqual(doc["beats"][0]["code_callout"]["cite"], "check.rs:1-2")
        self.assertEqual(self._cuts(dropped), [("check", "check.rs:1-4", "check.rs:1-2", (3,))])

    def test_the_report_names_runs_of_cut_lines(self):
        self.assertEqual(
            str(DroppedLines("b", "x.py:1-7", "x.py:1-2", (3, 4, 5, 7))),
            "dropped lines 3-5, 7 from a cite in beat 'b': no evidence token reaches "
            "them, so x.py:1-7 is written as x.py:1-2",
        )

    def test_a_claims_cut_is_reported_after_its_callouts(self):
        # The claim's cut names line 92 alone: line 93 is a closing brace, and
        # cutting a line that cannot hold evidence costs nothing.
        beat = _code_beat(
            id="long",
            cite_start=90,
            cite_end=91,
            caption="the multiplier is 0.5",
            caption_cite={"focus_index": 0, "start": 91, "end": 93},
        )
        with evidence_reaching(1):
            doc, dropped = _expanded([beat, _code_beat(id="companion")])
        self.assertEqual(doc["beats"][0]["claims"][0]["cite"], "decay.rs:91")
        self.assertEqual(
            self._cuts(dropped),
            [
                ("long", "decay.rs:90-91", "decay.rs:90", (91,)),
                ("long", "decay.rs:91-93", "decay.rs:91", (92,)),
            ],
        )

    def test_cutting_only_a_line_that_cannot_hold_evidence_is_not_reported(self):
        # "grounding_floor" is on lines 90 and 92, so the evidence still
        # reaches 92 and the trim cuts only the closing brace on 93.
        beat = _code_beat(id="long", cite_start=90, cite_end=93)
        with evidence_reaching(1):
            doc, dropped = _expanded([beat, _code_beat(id="companion")])
        self.assertEqual(doc["beats"][0]["code_callout"]["cite"], "decay.rs:90-92")
        self.assertEqual(dropped, ())

    def test_a_dropped_beat_reports_only_its_drop(self):
        # The callout's cut is moot once the weak caption cite drops the beat.
        gone = _code_beat(
            id="gone",
            cite_start=90,
            cite_end=91,
            caption="just a closing brace",
            caption_cite={"focus_index": 0, "start": 93, "end": 93},
        )
        with evidence_reaching(1):
            _, dropped = _expanded([gone, _code_beat(), _code_beat(id="companion")])
        self.assertEqual([type(d) for d in dropped], [DroppedBeat])
        self.assertEqual(dropped[0].beat_id, "gone")


if __name__ == "__main__":
    unittest.main()
