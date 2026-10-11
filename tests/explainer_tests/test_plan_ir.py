"""Tests for explainer.plan.plan_ir.

output_config.format supports only a restricted JSON Schema dialect (no
recursion, no numeric or string bounds), so everything it cannot express is
re-checked here after parsing. Each test below pins one of those rules.
"""

import copy
import unittest

from explainer.plan.facts import FactSheet, FocusTarget
from explainer.plan.plan_ir import PLAN_SCHEMA, Plan, PlanError, parse

TARGET = FocusTarget(
    path="a.py",
    start=10,
    end=20,
    text="\n".join("l%d" % i for i in range(10, 21)),
    language="python",
    kind="code",
)
SHEET = FactSheet(
    repo_root="/tmp/repo",
    commit="abc1234",
    dirty=False,
    topic="t",
    targets=(TARGET,),
    beat_budget=4,
)

RAW = {
    "title": "T",
    "subtitle": "S",
    "beats": [
        {
            "id": "b1",
            "canvas_kind": "code",
            "focus_index": 0,
            "cite_start": 11,
            "cite_end": 12,
            "display": "one\ntwo",
            "narration": "n",
            "caption": "",
            "caption_cite": None,
            "regions": [{"lines": [1], "narration": "r"}],
            "diagram": None,
        }
    ],
}


def _raw(**beat_overrides):
    doc = copy.deepcopy(RAW)
    doc["beats"][0].update(beat_overrides)
    return doc


class SchemaShapeTests(unittest.TestCase):
    def test_every_object_forbids_extra_properties(self):
        def walk(node):
            if isinstance(node, dict):
                if node.get("type") == "object":
                    self.assertFalse(node.get("additionalProperties", True))
                    self.assertIn("required", node)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(PLAN_SCHEMA)

    def test_schema_declares_no_unsupported_bounds(self):
        text = repr(PLAN_SCHEMA)
        for unsupported in ("minimum", "maximum", "minLength", "maxLength", "$ref"):
            self.assertNotIn(unsupported, text)


class ParseTests(unittest.TestCase):
    def test_parses_a_valid_plan(self):
        plan = parse(RAW, SHEET)
        self.assertIsInstance(plan, Plan)
        self.assertEqual(plan.beats[0].id, "b1")
        self.assertEqual(plan.beats[0].regions[0].lines, (1,))

    def test_focus_index_out_of_range_rejected(self):
        with self.assertRaises(PlanError):
            parse(_raw(focus_index=7), SHEET)

    def test_cite_outside_focus_target_rejected(self):
        with self.assertRaises(PlanError):
            parse(_raw(cite_start=1, cite_end=2), SHEET)

    def test_region_line_past_display_rejected(self):
        with self.assertRaises(PlanError):
            parse(_raw(regions=[{"lines": [9], "narration": "r"}]), SHEET)

    def test_caption_cite_outside_focus_target_rejected(self):
        bad = {"focus_index": 0, "start": 1, "end": 2}
        with self.assertRaises(PlanError):
            parse(_raw(caption_cite=bad), SHEET)

    def test_caption_cite_without_a_caption_rejected(self):
        # F5: a caption_cite makes a verified claim about the SOURCE; a
        # caption is the on-screen text the claim is supposedly proving. One
        # without the other is a contradiction that must be rejected at this
        # trust boundary -- expand.py has no display to attach it to and no
        # basis to invent one.
        good_cite = {"focus_index": 0, "start": 11, "end": 11}
        with self.assertRaisesRegex(PlanError, "caption"):
            parse(_raw(caption="", caption_cite=good_cite), SHEET)

    def test_caption_with_caption_cite_is_accepted(self):
        # Companion positive case: caption + caption_cite together are fine.
        good_cite = {"focus_index": 0, "start": 11, "end": 11}
        plan = parse(_raw(caption="the value is eleven", caption_cite=good_cite), SHEET)
        self.assertEqual(plan.beats[0].caption, "the value is eleven")
        self.assertIsNotNone(plan.beats[0].caption_cite)

    def test_diagram_beat_carrying_a_display_rejected(self):
        with self.assertRaises(PlanError):
            parse(
                _raw(
                    canvas_kind="diagram",
                    display="x",
                    diagram={
                        "nodes": [
                            {"id": "a", "shape": "dot", "label": "a", "color": "", "role": "source"}
                        ],
                        "edges": [],
                    },
                ),
                SHEET,
            )

    def test_diagram_beat_without_topology_rejected(self):
        with self.assertRaises(PlanError):
            parse(_raw(canvas_kind="diagram", display="", diagram=None), SHEET)

    def test_edge_referencing_unknown_node_rejected(self):
        topology = {
            "nodes": [{"id": "a", "shape": "dot", "label": "a", "color": "", "role": "source"}],
            "edges": [
                {
                    "kind": "arrow",
                    "from": "a",
                    "to": "ghost",
                    "label": "",
                    "narration": "",
                    "color": "",
                }
            ],
        }
        with self.assertRaises(PlanError):
            parse(_raw(canvas_kind="diagram", display="", diagram=topology), SHEET)

    def test_bad_node_id_rejected(self):
        topology = {
            "nodes": [{"id": "9bad", "shape": "dot", "label": "a", "color": "", "role": "source"}],
            "edges": [],
        }
        with self.assertRaises(PlanError):
            parse(_raw(canvas_kind="diagram", display="", diagram=topology), SHEET)

    def test_too_many_nodes_rejected(self):
        nodes = [
            {"id": "n%d" % i, "shape": "dot", "label": "", "color": "", "role": "middle"}
            for i in range(14)
        ]
        with self.assertRaises(PlanError):
            parse(
                _raw(canvas_kind="diagram", display="", diagram={"nodes": nodes, "edges": []}),
                SHEET,
            )

    def test_empty_beats_rejected(self):
        doc = copy.deepcopy(RAW)
        doc["beats"] = []
        with self.assertRaises(PlanError):
            parse(doc, SHEET)

    def test_missing_narration_rejected(self):
        with self.assertRaises(PlanError):
            parse(_raw(narration="   "), SHEET)

    def test_bad_node_color_rejected(self):
        # regions=[] isolates this from the (unrelated) region/display-length
        # check: a diagram beat's leftover default region would itself be
        # rejected first and mask whether the color check ever ran.
        topology = {
            "nodes": [
                {"id": "a", "shape": "dot", "label": "a", "color": "chartreuse", "role": "source"}
            ],
            "edges": [],
        }
        with self.assertRaisesRegex(PlanError, "color"):
            parse(_raw(canvas_kind="diagram", display="", diagram=topology, regions=[]), SHEET)

    def test_bad_node_role_rejected(self):
        topology = {
            "nodes": [{"id": "a", "shape": "dot", "label": "a", "color": "", "role": "bogus"}],
            "edges": [],
        }
        with self.assertRaisesRegex(PlanError, "role"):
            parse(_raw(canvas_kind="diagram", display="", diagram=topology, regions=[]), SHEET)

    def test_bad_edge_color_rejected(self):
        topology = {
            "nodes": [
                {"id": "a", "shape": "dot", "label": "a", "color": "", "role": "source"},
                {"id": "b", "shape": "dot", "label": "b", "color": "", "role": "sink"},
            ],
            "edges": [
                {
                    "kind": "arrow",
                    "from": "a",
                    "to": "b",
                    "label": "",
                    "narration": "",
                    "color": "chartreuse",
                }
            ],
        }
        with self.assertRaisesRegex(PlanError, "color"):
            parse(_raw(canvas_kind="diagram", display="", diagram=topology, regions=[]), SHEET)

    def test_null_node_id_rejected(self):
        # A JSON null must be rejected like an empty id, not silently become
        # the string "None" (which would satisfy _ID_RE).
        topology = {
            "nodes": [{"id": None, "shape": "dot", "label": "a", "color": "", "role": "source"}],
            "edges": [],
        }
        with self.assertRaisesRegex(PlanError, "identifier"):
            parse(_raw(canvas_kind="diagram", display="", diagram=topology, regions=[]), SHEET)

    def test_diagram_beat_display_normalized_to_empty_string(self):
        topology = {
            "nodes": [{"id": "a", "shape": "dot", "label": "a", "color": "", "role": "source"}],
            "edges": [],
        }
        plan = parse(
            _raw(canvas_kind="diagram", display="   ", diagram=topology, regions=[]),
            SHEET,
        )
        self.assertEqual(plan.beats[0].display, "")

    def test_diagram_beat_with_empty_lines_region_rejected(self):
        # A diagram beat has no display, so it carries no regions at all --
        # not even one whose own "lines" list happens to be empty.
        topology = {
            "nodes": [{"id": "a", "shape": "dot", "label": "a", "color": "", "role": "source"}],
            "edges": [],
        }
        with self.assertRaises(PlanError):
            parse(
                _raw(
                    canvas_kind="diagram",
                    display="",
                    diagram=topology,
                    regions=[{"lines": [], "narration": "x"}],
                ),
                SHEET,
            )


class MalformedInputTests(unittest.TestCase):
    """Arbitrary-shaped model JSON must fail closed as PlanError.

    Each case below is a wrong-type value that previously reached a bare
    TypeError/ValueError/AttributeError before it could be turned into the
    PlanError a caller is expected to catch.
    """

    def test_region_line_none_is_a_plan_error(self):
        with self.assertRaises(PlanError):
            parse(_raw(regions=[{"lines": [None], "narration": "r"}]), SHEET)

    def test_region_line_non_numeric_string_is_a_plan_error(self):
        with self.assertRaises(PlanError):
            parse(_raw(regions=[{"lines": ["abc"], "narration": "r"}]), SHEET)

    def test_region_lines_as_mapping_is_a_plan_error(self):
        with self.assertRaises(PlanError):
            parse(_raw(regions=[{"lines": {"a": 1}, "narration": "r"}]), SHEET)

    def test_diagram_nodes_as_string_is_a_plan_error(self):
        with self.assertRaises(PlanError):
            parse(
                _raw(canvas_kind="diagram", display="", diagram={"nodes": "oops", "edges": []}),
                SHEET,
            )

    def test_diagram_node_none_is_a_plan_error(self):
        with self.assertRaises(PlanError):
            parse(
                _raw(canvas_kind="diagram", display="", diagram={"nodes": [None], "edges": []}),
                SHEET,
            )

    def test_diagram_node_non_dict_is_a_plan_error(self):
        with self.assertRaises(PlanError):
            parse(
                _raw(canvas_kind="diagram", display="", diagram={"nodes": [5], "edges": []}),
                SHEET,
            )

    def test_diagram_edge_none_is_a_plan_error(self):
        topology = {
            "nodes": [{"id": "a", "shape": "dot", "label": "a", "color": "", "role": "source"}],
            "edges": [None],
        }
        with self.assertRaises(PlanError):
            parse(_raw(canvas_kind="diagram", display="", diagram=topology), SHEET)

    def test_null_edge_endpoint_rejected(self):
        # A JSON null "from" must be rejected like an empty endpoint, not
        # silently stringify to "None" and resolve against a node genuinely
        # (and validly) named "None".
        topology = {
            "nodes": [
                {"id": "None", "shape": "dot", "label": "a", "color": "", "role": "source"},
                {"id": "b", "shape": "dot", "label": "b", "color": "", "role": "sink"},
            ],
            "edges": [
                {
                    "kind": "arrow",
                    "from": None,
                    "to": "b",
                    "label": "",
                    "narration": "",
                    "color": "",
                }
            ],
        }
        with self.assertRaisesRegex(PlanError, "unknown node"):
            parse(_raw(canvas_kind="diagram", display="", diagram=topology, regions=[]), SHEET)

    def test_null_edge_to_endpoint_rejected(self):
        topology = {
            "nodes": [
                {"id": "a", "shape": "dot", "label": "a", "color": "", "role": "source"},
                {"id": "None", "shape": "dot", "label": "b", "color": "", "role": "sink"},
            ],
            "edges": [
                {
                    "kind": "arrow",
                    "from": "a",
                    "to": None,
                    "label": "",
                    "narration": "",
                    "color": "",
                }
            ],
        }
        with self.assertRaisesRegex(PlanError, "unknown node"):
            parse(_raw(canvas_kind="diagram", display="", diagram=topology, regions=[]), SHEET)

    def test_bool_focus_index_rejected(self):
        # bool is an int subclass in Python. False == 0, a genuinely valid
        # index into SHEET.targets, so a naive isinstance(x, int) check lets
        # it through *silently* (no error at all) -- isolating this from the
        # unrelated "out of range" rejection that True (== 1, out of range
        # for this single-target sheet) would trigger either way.
        with self.assertRaisesRegex(PlanError, "focus_index"):
            parse(_raw(focus_index=False), SHEET)

    def test_bool_cite_start_rejected(self):
        # Uses a dedicated 1-line target (range 1-1) so True (== 1) also
        # satisfies the *range* check, isolating this from the unrelated
        # "outside focus target" rejection the default 10-20 target would
        # trigger regardless of the bool bug.
        target = FocusTarget(path="a.py", start=1, end=1, text="x", language="python", kind="code")
        sheet = FactSheet(
            repo_root="/tmp/repo",
            commit="abc1234",
            dirty=False,
            topic="t",
            targets=(target,),
            beat_budget=1,
        )
        doc = copy.deepcopy(RAW)
        doc["beats"][0]["cite_start"] = True
        doc["beats"][0]["cite_end"] = True
        with self.assertRaisesRegex(PlanError, "integer"):
            parse(doc, sheet)

    def test_bool_region_line_rejected(self):
        with self.assertRaisesRegex(PlanError, "integer"):
            parse(_raw(regions=[{"lines": [True], "narration": "r"}]), SHEET)

    def test_diagram_nodes_falsy_wrong_type_rejected(self):
        # `raw.get("nodes") or []` collapses a falsy wrong type (0, {}, "")
        # to [] before the list-type check runs, so it never fires for these
        # -- only for a truthy wrong type like "oops". Falsy wrong types
        # must be rejected too, not silently treated as an empty list.
        with self.assertRaisesRegex(PlanError, "list"):
            parse(
                _raw(canvas_kind="diagram", display="", diagram={"nodes": 0, "edges": []}),
                SHEET,
            )

    def test_diagram_edges_falsy_wrong_type_rejected(self):
        # regions=[] isolates this from the (unrelated) region/display-length
        # check: a diagram beat's leftover default region would itself be
        # rejected first (display has 0 lines) and mask whether the edges
        # list-type check ever ran.
        topology = {
            "nodes": [{"id": "a", "shape": "dot", "label": "a", "color": "", "role": "source"}],
            "edges": {},
        }
        with self.assertRaisesRegex(PlanError, "list"):
            parse(_raw(canvas_kind="diagram", display="", diagram=topology, regions=[]), SHEET)

    def test_regions_falsy_wrong_type_rejected(self):
        with self.assertRaisesRegex(PlanError, "list"):
            parse(_raw(regions=""), SHEET)

    def test_region_lines_falsy_wrong_type_rejected(self):
        with self.assertRaisesRegex(PlanError, "list"):
            parse(_raw(regions=[{"lines": 0, "narration": "r"}]), SHEET)

    def test_null_node_shape_normalizes_to_empty_not_the_string_none(self):
        # P3: id/color/role already guard with `or ""` so a JSON null
        # normalizes like a missing key would; shape did not, so it silently
        # stringified to the word "None" instead. "None" also happens not to
        # be a valid shape, so this was rejected either way -- just with a
        # message naming a string that was never actually in the document.
        topology = {
            "nodes": [{"id": "a", "shape": None, "label": "a", "color": "", "role": "source"}],
            "edges": [],
        }
        with self.assertRaises(PlanError) as caught:
            parse(_raw(canvas_kind="diagram", display="", diagram=topology, regions=[]), SHEET)
        self.assertIn("bad shape ''", str(caught.exception))
        self.assertNotIn("None", str(caught.exception))

    def test_null_edge_kind_normalizes_to_empty_not_the_string_none(self):
        # Same guard, the PlanEdge sibling of the case above: from/to/label/
        # narration/color already have `or ""`; kind did not.
        topology = {
            "nodes": [
                {"id": "a", "shape": "dot", "label": "a", "color": "", "role": "source"},
                {"id": "b", "shape": "dot", "label": "b", "color": "", "role": "sink"},
            ],
            "edges": [
                {"kind": None, "from": "a", "to": "b", "label": "", "narration": "", "color": ""}
            ],
        }
        with self.assertRaises(PlanError) as caught:
            parse(_raw(canvas_kind="diagram", display="", diagram=topology, regions=[]), SHEET)
        self.assertIn("bad kind ''", str(caught.exception))
        self.assertNotIn("None", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
