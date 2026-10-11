"""Tests for diagram codegen: the emitted scene must compile and stage right."""

import unittest

from explainer.codegen.manim_codegen import generate_scene
from explainer.storyboard.model import (
    Beat,
    Canvas,
    Claim,
    DiaEdge,
    DiaMotion,
    DiaNode,
    DiaPath,
    Diagram,
    Endpoint,
    Source,
    Storyboard,
    Theme,
)


def _sb(diagram, narration="five forces"):
    beat = Beat(id="b1", canvas=Canvas(kind="diagram", diagram=diagram), narration=narration)
    return Storyboard(
        version=1,
        title="T",
        source=Source(repo_root="/tmp/r", commit="abcd123"),
        beats=(beat,),
        theme=Theme(),
    )


class DiagramCodegenTests(unittest.TestCase):
    def test_minimal_diagram_compiles(self):
        dia = Diagram(
            nodes=(
                DiaNode(id="agent", shape="dot", at="left", label="agent"),
                DiaNode(id="goal", shape="ring", at="right", label="goal"),
            )
        )
        src = generate_scene(_sb(dia), scene_name="Demo")
        compile(src, "<diagram>", "exec")  # must be valid Python
        self.assertIn("class Demo(VoiceoverScene)", src)
        self.assertNotIn("agent =", src)  # author ids never become identifiers
        self.assertIn("node0", src)


class DiagramFullEmitTests(unittest.TestCase):
    def _full(self):
        dia = Diagram(
            nodes=(
                DiaNode(id="agent", shape="dot", at="left", label="agent"),
                DiaNode(id="goal", shape="ring", at="right", label="goal", color="gold"),
            ),
            edges=(
                DiaEdge(
                    kind="arrow",
                    frm=Endpoint(node="agent"),
                    to=Endpoint(node="goal"),
                    color="blue",
                    label="directional",
                    narration="pushes",
                ),
                DiaEdge(kind="vbar", at="center", height=2.0, color="red", label="wall"),
            ),
            path=DiaPath(
                frm=Endpoint(node="agent"), to=Endpoint(node="goal"), bow="down", color="teal"
            ),
            motion=DiaMotion(node="agent", narration="moves"),
        )
        return generate_scene(_sb(dia), scene_name="Full")

    def test_full_diagram_compiles(self):
        compile(self._full(), "<full>", "exec")

    def test_emits_edge_mark_path_and_motion(self):
        src = self._full()
        self.assertIn("Arrow(", src)
        self.assertIn("Line(", src)  # vbar mark
        self.assertIn("CubicBezier(", src)  # path
        self.assertIn("MoveAlongPath(", src)

    def test_static_nodes_dim_during_motion(self):
        src = self._full()
        # the non-moving ring node (goal = node1) dims via STROKE (set_opacity would
        # fill the ring into a disc); a stroke-only edge dims the same way.
        self.assertIn("node1.animate.set_stroke(opacity=0.3)", src)
        self.assertIn("edge0.animate.set_stroke(opacity=0.3)", src)
        self.assertNotIn("edge0.animate.set_opacity(0.3)", src)
        # the moving node's label is faded so it does not orphan
        self.assertIn("FadeOut(node0_label)", src)

    def test_node_and_edge_labels_fade_in_not_write(self):
        # Write() leaves partial-glyph mid-frames under long voiceovers.
        src = self._full()
        self.assertIn("FadeIn(node0_label)", src)
        self.assertIn("FadeIn(edge0_label)", src)
        self.assertNotIn("Write(node0_label)", src)
        self.assertNotIn("Write(edge0_label)", src)
        self.assertNotIn("Write(", src)

    def test_cited_label_emits_provenance_badge(self):
        dia = Diagram(
            nodes=(
                DiaNode(
                    id="n", shape="dot", at="center", label="x", cite="f.py:1", evidence=("x",)
                ),
            ),
            claims=(Claim(cite="f.py:1", evidence=("x",)),),
        )
        src = generate_scene(_sb(dia))
        self.assertIn("verified @ abcd123", src)
        self.assertIn("simplified", src)
        self.assertIn("#3FB950", src)  # theme.accent_confirmed, not a hardcoded drift

    def test_provenance_fades_in_before_motion(self):
        dia = Diagram(
            nodes=(
                DiaNode(id="agent", shape="dot", at="left", label="agent"),
                DiaNode(id="goal", shape="ring", at="right", label="goal"),
            ),
            edges=(
                DiaEdge(
                    kind="arrow",
                    frm=Endpoint(node="agent"),
                    to=Endpoint(node="goal"),
                    label="directional",
                    narration="pushes",
                    cite="f.py:1",
                    evidence=("x",),
                ),
            ),
            path=DiaPath(
                frm=Endpoint(node="agent"), to=Endpoint(node="goal"), bow="down", color="teal"
            ),
            motion=DiaMotion(node="agent", narration="moves"),
            claims=(Claim(cite="f.py:1", evidence=("x",)),),
        )
        src = generate_scene(_sb(dia))
        self.assertEqual(src.count("FadeIn(provenance)"), 1)
        self.assertLess(src.index("FadeIn(provenance)"), src.index("MoveAlongPath"))
        # Must appear before narrated edge VO, not only before motion.
        self.assertLess(src.index("FadeIn(provenance)"), src.index("text='pushes'"))

    def test_stage_holds_when_more_steps_follow(self):
        # Stage reveal gets a short post_wait so composed glyphs are readable
        # before narrated edges / motion start.
        src = self._full()
        self.assertIn("self.wait(0.35)", src)

    def test_confirmed_color_resolves_to_theme_accent(self):
        dia = Diagram(
            nodes=(DiaNode(id="a", shape="dot", at="center", label="x", color="confirmed"),)
        )
        src = generate_scene(_sb(dia))
        self.assertIn("#3FB950", src)  # Theme().accent_confirmed
