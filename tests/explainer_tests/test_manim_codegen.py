"""Tests for explainer.codegen.manim_codegen (Storyboard -> Manim source string).

Deterministic, no real render: assert the generated source is valid Python and
has the expected structure, that authored strings are safely embedded, and that
an unsafe plot function is rejected.
"""

import dataclasses
import os
import unittest

from explainer.codegen.manim_codegen import (
    VOICEOVER_VISUAL_CAP_S,
    CodegenError,
    generate_scene,
)
from explainer.storyboard.model import (
    Beat,
    Canvas,
    CodeCallout,
    DiaEdge,
    DiaNode,
    Diagram,
    Endpoint,
    Region,
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


class CodegenTests(unittest.TestCase):
    def setUp(self):
        self.sb = load_storyboard(EXAMPLE)
        self.src = generate_scene(self.sb)

    def test_generated_source_is_valid_python(self):
        compile(self.src, "<generated>", "exec")  # raises SyntaxError if malformed

    def test_emits_voiceover_scene(self):
        self.assertIn("class ExplainerVideo(VoiceoverScene):", self.src)
        # The speech service comes from tts_compat (persistent cache + retry), not
        # a bare GTTSService: a transient gTTS blip must not kill a whole render.
        self.assertIn("self.set_speech_service(build_speech_service(lang='en'))", self.src)
        self.assertNotIn("GTTSService(", self.src)

    def test_one_voiceover_block_per_narration(self):
        # per-line narration (1) + floor-and-cap narration (1) + its 2 regions (2)
        # + no-proof-no-badge narration (1) + its 1 region (1) = 6
        self.assertEqual(self.src.count("with self.voiceover("), 6)

    def test_emits_cited_code_callouts(self):
        self.assertEqual(self.src.count("build_code("), 3)
        self.assertIn("TOKENS_PER_LINE = 2", self.src)
        # The scene shows the callout's DISPLAY (a teaching simplification), not the
        # source: "min(MAX_TOKENS, wanted)" appears, the source-only evidence does not.
        self.assertIn("min(MAX_TOKENS, wanted)", self.src)
        self.assertNotIn("TOKENS_PER_LINE * line_count", self.src)

    def test_plots_curves_as_validated_lambdas(self):
        self.assertIn("axes.plot(lambda x: 2 * x", self.src)
        self.assertIn("min(24, max(5, 2 * x))", self.src)

    def test_highlights_regions_per_line(self):
        self.assertIn("code_line(code, 1)", self.src)
        self.assertIn("code_line(code, 2)", self.src)

    def test_emits_title_card(self):
        self.assertIn(repr(self.sb.title), self.src)
        self.assertIn(repr(self.sb.subtitle), self.src)
        self.assertIn("title_strip", self.src)
        self.assertIn(repr(self.sb.title.replace(" ", "  ")), self.src)

    def test_region_boxes_crossfade_not_morph(self):
        # Region highlights swap via FadeOut+FadeIn (no ReplacementTransform), so a
        # box never sweeps across the code as a strikethrough during the tween.
        self.assertNotIn("ReplacementTransform", self.src)
        self.assertIn("FadeOut(box", self.src)
        self.assertIn("FadeIn(box", self.src)

    def test_highlight_boxes_use_tight_thin_code_line_rectangles(self):
        self.assertIn(
            "SurroundingRectangle(code_line(code, 1), color='#3FB950', "
            "buff=0.025, stroke_width=2)",
            self.src,
        )
        self.assertIn(
            "SurroundingRectangle(code_line(code, 2), color='#FFD479', "
            "buff=0.025, stroke_width=2)",
            self.src,
        )
        self.assertNotIn("stretch_to_fit_height", self.src)

    def test_annotation_label_is_nudged_inside_plot(self):
        # right-aligned above the line: clears the left end of the curves and sits below the legend
        self.assertIn(".align_to(ann0_line, RIGHT).shift(LEFT * 0.3)", self.src)

    def test_labels_use_sans_font_without_ligatures(self):
        self.assertIn("font='Arial'", self.src)
        self.assertIn("disable_ligatures=True", self.src)

    def test_uses_centered_content_group(self):
        self.assertIn("plot_group = VGroup(", self.src)
        self.assertIn(".arrange(DOWN", self.src)

    def test_bound_annotations_are_labeled(self):
        # Both bounds the narration names must be marked on screen, not left to color.
        self.assertIn("24 CAP", self.src)
        self.assertIn("5 FLOOR", self.src)

    def test_emits_citation_provenance(self):
        # PD-01: the differentiator must be visible IN the video -- each code callout
        # shows its cite (basename:line) and a verified badge carrying the source commit.
        self.assertIn("provenance = VGroup(", self.src)
        self.assertIn("evidence.py:56", self.src)
        self.assertIn("evidence.py:205-211", self.src)
        # The example cites its own repo and pins no commit (a pin would go stale
        # on the next commit), so its badge is the bare 'verified'.
        self.assertIn("'verified'", self.src)
        self.assertNotIn("verified @", self.src)
        self.assertIn("FadeIn(code_block)", self.src)

    def test_badge_carries_pinned_commit(self):
        # A storyboard that pins meta.source.commit shows it on the badge.
        source = dataclasses.replace(self.sb.source, commit="abc1234")
        src = generate_scene(dataclasses.replace(self.sb, source=source))
        self.assertIn("verified @ abc1234", src)

    def test_callout_marked_simplified(self):
        # PD-02 / decision 1: on-screen code is labeled a simplified view, never shown
        # as verbatim source; the verified-source badge still cites the real lines.
        self.assertIn("simplified", self.src)
        self.assertIn("'verified'", self.src)

    def test_curve_labels_reach_the_screen(self):
        # CG-03/OQ-01: authored curves[].label must render (a colored legend), so the
        # reveal is not encoded by color alone. Every authored label must appear.
        self.assertIn("legend = VGroup(", self.src)
        self.assertIn("2 tokens per line", self.src)
        self.assertIn("Budget: floor 5, cap 24", self.src)
        self.assertIn("FadeIn(legend)", self.src)

    def test_curve_fn_with_comment_still_compiles(self):
        # CG-01: a '#'-comment fn passes the plot_expr whitelist; codegen must embed
        # the NORMALIZED source so it cannot comment out the generated plot() call.
        sb = Storyboard(
            version=1,
            title="t",
            source=Source(repo_root="/tmp"),
            theme=Theme(),
            beats=(
                Beat(
                    id="b",
                    canvas=Canvas(kind="plot", spec={"curves": [{"id": "c", "fn": "0  # )"}]}),
                    narration="n",
                ),
            ),
        )
        src = generate_scene(sb)
        compile(src, "<generated>", "exec")  # must not raise SyntaxError
        self.assertNotIn("lambda x: 0  #", src)

    def _plot_beat(self, callout=None, spec=None):
        return Storyboard(
            version=1,
            title="t",
            source=Source(repo_root="/tmp"),
            theme=Theme(),
            beats=(
                Beat(
                    id="b",
                    canvas=Canvas(kind="plot", spec=spec or {}),
                    narration="n",
                    code_callout=callout,
                ),
            ),
        )

    def test_region_without_narration_highlights_without_voiceover(self):
        # A highlight-only region (empty narration) must NOT wrap its play in a
        # voiceover block: that would send an empty string to gTTS at render time.
        sb = self._plot_beat(
            CodeCallout(
                display="line one\nline two",
                language="text",
                cite="a.rs:1",
                regions=(Region(lines=(2,), narration=""),),
            )
        )
        src = generate_scene(sb)
        compile(src, "<generated>", "exec")
        self.assertNotIn("self.voiceover(text='')", src)
        self.assertIn("box0", src)  # the highlight itself still happens

    def test_whitespace_only_region_narration_also_takes_silent_path(self):
        # "   " is truthy but unspeakable: gTTS asserts on normalized-empty text.
        sb = self._plot_beat(
            CodeCallout(
                display="line one\nline two",
                language="text",
                cite="a.rs:1",
                regions=(Region(lines=(1,), narration="   "),),
            )
        )
        src = generate_scene(sb)
        compile(src, "<generated>", "exec")
        self.assertNotIn("self.voiceover(text='   ')", src)
        self.assertIn("box0", src)

    def test_region_line_out_of_range_raises(self):
        # CG-04: a region citing a display line that does not exist must fail at codegen,
        # not raise IndexError at render time.
        sb = self._plot_beat(
            CodeCallout(
                display="line one\nline two",
                language="rust",
                cite="a.rs:1",
                regions=(Region(lines=(5,)),),
            )
        )
        with self.assertRaises(CodegenError):
            generate_scene(sb)

    def test_multiline_region_surrounds_all_lines(self):
        # CG-05: a region spanning multiple lines highlights all of them, not just the first.
        sb = self._plot_beat(
            CodeCallout(
                display="a\nb\nc",
                language="rust",
                cite="a.rs:1",
                regions=(Region(lines=(1, 2)),),
            )
        )
        self.assertIn("VGroup(code_line(code, 1), code_line(code, 2))", generate_scene(sb))

    def test_unknown_annotation_kind_raises(self):
        # CG-06: an unsupported annotation kind is a clear error, not a silent skip.
        sb = self._plot_beat(spec={"annotations": [{"kind": "vline", "x": 5}]})
        with self.assertRaises(CodegenError):
            generate_scene(sb)

    def test_annotation_y_string_is_coerced(self):
        # CG-06: a string y must coerce to float, not crash the :g format.
        sb = self._plot_beat(spec={"annotations": [{"kind": "hline", "y": "0.4", "label": "F"}]})
        src = generate_scene(sb)
        compile(src, "<generated>", "exec")
        self.assertIn("axes.c2p(0, 0.4)", src)

    def test_beat_with_no_visual_content_raises(self):
        # decision 3 / ARCH-03: a beat that yields no visual mobjects (non-plot canvas,
        # no callout) must raise, never emit a silent blank narrated scene.
        blank = Storyboard(
            version=1,
            title="t",
            source=Source(repo_root="/tmp"),
            theme=Theme(),
            beats=(Beat(id="b", canvas=Canvas(kind="code", spec={}), narration="n"),),
        )
        with self.assertRaises(CodegenError):
            generate_scene(blank)

    def test_code_canvas_renders_centered_code_without_plot(self):
        # 'code' canvas: a code-centric beat renders the callout with no Axes.
        sb = Storyboard(
            version=1,
            title="t",
            source=Source(repo_root="/tmp", commit="abc123"),
            theme=Theme(),
            beats=(
                Beat(
                    id="b",
                    canvas=Canvas(kind="code", spec={}),
                    narration="n",
                    code_callout=CodeCallout(
                        display="let x = 0;",
                        language="rust",
                        cite="a.rs:1",
                        evidence=("x",),
                    ),
                ),
            ),
        )
        src = generate_scene(sb)
        compile(src, "<generated>", "exec")
        self.assertIn("build_code(", src)
        self.assertNotIn("axes = Axes", src)  # no plot on a code canvas
        self.assertIn("code_block.move_to(ORIGIN)", src)

    def test_rejects_unsafe_curve_fn(self):
        bad = Storyboard(
            version=1,
            title="t",
            source=Source(repo_root="/tmp"),
            theme=Theme(),
            beats=(
                Beat(
                    id="b",
                    canvas=Canvas(kind="plot", spec={"curves": [{"id": "c", "fn": "eval('x')"}]}),
                    narration="n",
                ),
            ),
        )
        with self.assertRaises(CodegenError):
            generate_scene(bad)

    def test_authored_strings_are_safely_embedded(self):
        nasty = 'He said "hi"\nimport os  # injection attempt'
        sb = Storyboard(
            version=1,
            title="t",
            source=Source(repo_root="/tmp"),
            theme=Theme(),
            beats=(Beat(id="b", canvas=Canvas(kind="plot", spec={}), narration=nasty),),
        )
        src = generate_scene(sb)
        compile(src, "<generated>", "exec")  # still valid -> not raw-injected
        self.assertIn(repr(nasty), src)  # embedded as a repr literal
        self.assertNotIn("\n        import os  # injection attempt", src)

    def test_theme_background_sets_camera_color(self):
        sb = Storyboard(
            version=1,
            title="t",
            source=Source(repo_root="/tmp"),
            theme=Theme(background="#1a1814"),
            beats=(Beat(id="b", canvas=Canvas(kind="plot", spec={}), narration="n"),),
        )
        src = generate_scene(sb)
        compile(src, "<generated>", "exec")
        self.assertIn("self.camera.background_color = '#1a1814'", src)

    def test_no_background_emits_no_camera_line(self):
        sb = Storyboard(
            version=1,
            title="t",
            source=Source(repo_root="/tmp"),
            theme=Theme(),
            beats=(Beat(id="b", canvas=Canvas(kind="plot", spec={}), narration="n"),),
        )
        self.assertNotIn("self.camera.background_color", generate_scene(sb))

    def test_narrated_reveal_caps_play_duration(self):
        # Diagram edge narration goes through _emit_reveal_steps: play must not
        # stretch Create/FadeIn across the full gTTS line.
        dia = Diagram(
            nodes=(
                DiaNode(id="a", shape="dot", at="left", label="a"),
                DiaNode(id="b", shape="ring", at="right", label="b"),
            ),
            edges=(
                DiaEdge(
                    kind="arrow",
                    frm=Endpoint(node="a"),
                    to=Endpoint(node="b"),
                    label="directional",
                    narration="Directional force pushes toward the goal.",
                ),
            ),
        )
        sb = Storyboard(
            version=1,
            title="t",
            source=Source(repo_root="/tmp"),
            theme=Theme(),
            beats=(
                Beat(
                    id="b",
                    canvas=Canvas(kind="diagram", diagram=dia),
                    narration="A diagram decomposes.",
                ),
            ),
        )
        src = generate_scene(sb)
        compile(src, "<generated>", "exec")
        self.assertIn("with self.voiceover", src)
        self.assertIn(
            f"run_time=min({VOICEOVER_VISUAL_CAP_S}, tracker.duration)",
            src,
        )
        self.assertIn(
            "self.wait(max(0, tracker.duration - "
            f"min({VOICEOVER_VISUAL_CAP_S}, tracker.duration)))",
            src,
        )
        # Old stretchy path must be gone from reveal steps.
        self.assertNotIn("run_time=tracker.duration)", src)


if __name__ == "__main__":
    unittest.main()
