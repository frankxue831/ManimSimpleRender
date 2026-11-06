"""Tests for diagram codegen: the emitted scene must compile and stage right."""

import importlib.util
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
                DiaNode(id="context", shape="ring", at="lower-center", label="context"),
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

    def test_direct_typed_storyboard_cannot_bypass_motion_start_check(self):
        from explainer.codegen.manim_codegen import CodegenError

        diagram = Diagram(
            nodes=(DiaNode(id="job", shape="dot", at="left", label="job7"),),
            path=DiaPath(frm=Endpoint(anchor="center"), to=Endpoint(anchor="right")),
            motion=DiaMotion(node="job"),
        )
        with self.assertRaisesRegex(CodegenError, "path start"):
            generate_scene(_sb(diagram))

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
        # The named goal stays visible; other context dims via STROKE, without
        # filling a ring or edge into a solid shape.
        self.assertNotIn("node1.animate.set_stroke(opacity=0.3)", src)
        self.assertNotIn("node1_label.animate.set_opacity(0.3)", src)
        self.assertIn("node2.animate.set_stroke(opacity=0.3)", src)
        self.assertIn("edge0.animate.set_stroke(opacity=0.3)", src)
        self.assertNotIn("edge0.animate.set_opacity(0.3)", src)
        # Keep the existing label FadeOut until tracking layout is separately ready.
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


@unittest.skipUnless(
    importlib.util.find_spec("manim"), "existing Manim needed for generated geometry"
)
class DiagramLayeringRuntimeTests(unittest.TestCase):
    """Optional actual Manim geometry; no voice service or Scene execution."""

    def _setup(self, *, labeled=True, named_destination=True):
        import importlib.util

        if importlib.util.find_spec("manim") is None:
            self.skipTest("existing Manim needed for generated geometry")
        import manim
        from explainer.codegen.diagram_codegen import emit_diagram

        diagram = Diagram(
            nodes=(
                DiaNode(
                    id="job",
                    shape="dot",
                    at="left",
                    label="job 7" if labeled else "",
                ),
                DiaNode(
                    id="worker",
                    shape="ring",
                    at="right",
                    label="worker",
                ),
            ),
            path=DiaPath(
                frm=Endpoint(node="job"),
                to=Endpoint(node="worker") if named_destination else Endpoint(anchor="right"),
                bow="up",
            ),
            motion=DiaMotion(node="job"),
        )
        board = _sb(diagram)
        setup, steps = emit_diagram(board.beats[0], board)
        namespace = dict(vars(manim))
        source = "def build():\n" + "\n".join(setup) + "\n        return locals()\n"
        exec(compile(source, "<fixed diagram emitter>", "exec"), namespace)
        return namespace["build"](), steps, namespace

    def test_preparation_keeps_named_destination_and_label_authored_opacity(self):
        from manim.animation.animation import prepare_animation

        geometry, steps, namespace = self._setup()
        for expression in steps[-2].plays:
            animation = prepare_animation(eval(expression, namespace, geometry))
            animation.begin()
            animation.interpolate(1)
            animation.finish()
        self.assertAlmostEqual(geometry["node1"].get_stroke_opacity(), 1)
        label_members = geometry["node1_label"].family_members_with_points()
        self.assertTrue(label_members)
        self.assertTrue(all(member.get_fill_opacity() == 1 for member in label_members))

    def test_anchor_endpoint_does_not_guess_a_nearby_destination_identity(self):
        from manim.animation.animation import prepare_animation

        geometry, steps, namespace = self._setup(named_destination=False)
        for expression in steps[-2].plays:
            animation = prepare_animation(eval(expression, namespace, geometry))
            animation.begin()
            animation.interpolate(1)
            animation.finish()
        self.assertAlmostEqual(geometry["node1"].get_stroke_opacity(), 0.3)

    def test_motion_guide_cannot_cover_the_solid_actor(self):
        geometry, _, _ = self._setup()
        self.assertLess(geometry["path"].z_index, geometry["node0"].z_index)
        self.assertLess(geometry["path"].z_index, geometry["node0_label"].z_index)

    def test_unlabeled_motion_does_not_reference_an_undefined_label(self):
        geometry, steps, namespace = self._setup(labeled=False)
        self.assertNotIn("node0_label", geometry)
        self.assertEqual(steps[-1].plays, ["MoveAlongPath(node0, path)"])
