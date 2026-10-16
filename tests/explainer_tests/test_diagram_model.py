"""Tests for the typed diagram canvas model (parse + validation)."""

import os
import tempfile
import textwrap
import unittest

from explainer.storyboard.model import Endpoint, StoryboardError, load_storyboard


def _load(text):
    handle = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    handle.write(text)
    handle.close()
    try:
        return load_storyboard(handle.name)
    finally:
        os.unlink(handle.name)


VALID = textwrap.dedent("""
    version: 1
    meta: { title: "T", source: { repo_root: "/tmp/repo" } }
    beats:
      - id: b1
        canvas:
          kind: diagram
          spec:
            nodes:
              - {id: agent, shape: dot, at: left, label: agent}
              - {id: goal, shape: ring, at: right, label: goal, color: gold}
            edges:
              - {kind: arrow, from: {node: agent}, to: {node: goal},
                 color: blue, label: directional, narration: "pushes"}
              - {kind: vbar, at: center, height: 2.0, color: red, label: wall}
            path: {from: {node: agent}, to: {node: goal}, bow: down, color: teal}
            motion: {node: agent, narration: "moves"}
        narration: "five forces"
    """)


class DiagramHappyPathTests(unittest.TestCase):
    def test_parses_into_typed_diagram(self):
        sb = _load(VALID)
        dia = sb.beats[0].canvas.diagram
        self.assertIsNotNone(dia)
        self.assertEqual([n.id for n in dia.nodes], ["agent", "goal"])
        self.assertEqual(dia.nodes[0].shape, "dot")
        self.assertEqual(dia.edges[0].kind, "arrow")
        self.assertEqual(dia.edges[0].frm, Endpoint(node="agent"))
        self.assertEqual(dia.edges[0].to, Endpoint(node="goal"))
        self.assertEqual(dia.edges[1].kind, "vbar")
        self.assertEqual(dia.edges[1].at, "center")
        self.assertEqual(dia.path.bow, "down")
        self.assertEqual(dia.motion.node, "agent")

    def test_diagram_canvas_kind_is_diagram(self):
        sb = _load(VALID)
        self.assertEqual(sb.beats[0].canvas.kind, "diagram")


def _diagram(spec_body):
    return textwrap.dedent("""
        version: 1
        meta: { title: "T", source: { repo_root: "/tmp/repo" } }
        beats:
          - id: b1
            canvas:
              kind: diagram
              spec:
        %s
            narration: "n"
        """) % textwrap.indent(textwrap.dedent(spec_body), " " * 10)


class DiagramValidationTests(unittest.TestCase):
    def _bad(self, spec_body, needle=None):
        with self.assertRaises(StoryboardError) as cm:
            _load(_diagram(spec_body))
        if needle:
            self.assertIn(needle, str(cm.exception))

    def test_duplicate_node_id_rejected(self):
        self._bad(
            "nodes:\n  - {id: a, shape: dot, at: left}\n  - {id: a, shape: dot, at: right}",
            "a",
        )

    def test_bad_id_regex_rejected(self):
        self._bad("nodes:\n  - {id: '1bad', shape: dot, at: left}")

    def test_unknown_shape_rejected(self):
        self._bad("nodes:\n  - {id: a, shape: blob, at: left}", "blob")

    def test_unknown_anchor_rejected(self):
        self._bad("nodes:\n  - {id: a, shape: dot, at: nowhere}")

    def test_connector_missing_endpoints_rejected(self):
        self._bad("edges:\n  - {kind: arrow, label: x}")

    def test_endpoint_unknown_node_rejected(self):
        self._bad(
            "nodes:\n  - {id: a, shape: dot, at: left}\n"
            "edges:\n  - {kind: arrow, from: {node: a}, to: {node: ghost}}",
            "ghost",
        )

    def test_motion_without_path_rejected(self):
        self._bad(
            "nodes:\n  - {id: a, shape: dot, at: left}\n" "motion: {node: a}",
            "path",
        )

    def test_motion_node_not_dot_rejected(self):
        self._bad(
            "nodes:\n  - {id: a, shape: ring, at: left}\n  - {id: b, shape: dot, at: right}\n"
            "path: {from: {node: a}, to: {node: b}}\n"
            "motion: {node: a}"
        )

    def test_cite_without_evidence_rejected(self):
        self._bad("nodes:\n  - {id: a, shape: dot, at: left, cite: 'f.py:1'}", "evidence")

    def test_cite_with_token_that_matches_any_text_rejected(self):
        for token in ("''", "'//'"):
            with self.subTest(token=token):
                self._bad(
                    "nodes:\n  - {id: a, shape: dot, at: left, cite: 'f.py:1', "
                    "evidence: ['x', %s]}" % token,
                    "prove something",
                )

    def test_unknown_key_rejected(self):
        self._bad("nodes:\n  - {id: a, shape: dot, at: left, lable: oops}", "lable")

    def test_overlapping_nodes_rejected(self):
        self._bad(
            "nodes:\n  - {id: a, shape: dot, at: center}\n  - {id: b, shape: dot, at: center}"
        )

    def test_malformed_cite_rejected(self):
        self._bad("nodes:\n  - {id: a, shape: dot, at: left, cite: 'nocolon', evidence: ['x']}")


class DiagramCodeCalloutTests(unittest.TestCase):
    def test_diagram_with_code_callout_rejected(self):
        text = textwrap.dedent("""
            version: 1
            meta: { title: "T", source: { repo_root: "/tmp/repo" } }
            beats:
              - id: b1
                canvas: { kind: diagram, spec: { nodes: [{id: a, shape: dot, at: left}] } }
                narration: "n"
                code_callout: { display: "x", language: text, cite: "f.py:1" }
            """)
        with self.assertRaises(StoryboardError):
            _load(text)


if __name__ == "__main__":
    unittest.main()
