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
    X_NUMBER_ROW,
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


def _line_with(src, needle):
    """The one generated line containing ``needle``."""
    matches = [line for line in src.splitlines() if needle in line]
    if len(matches) != 1:
        raise AssertionError(f"{needle!r} on {len(matches)} lines")
    return matches[0]


def _play_after(src, marker):
    """The first ``self.play(`` line after the first line containing ``marker``."""
    lines = src.splitlines()
    start = next(i for i, line in enumerate(lines) if marker in line)
    return next(line for line in lines[start + 1 :] if "self.play(" in line)


def _line_after(src, line):
    """The generated line right after ``line``."""
    lines = src.splitlines()
    return lines[lines.index(line) + 1]


CAPPED_RUN_TIME = f"run_time=min({VOICEOVER_VISUAL_CAP_S}, tracker.duration))"
# safe_wait, not wait: a line no longer than the cap leaves 0 s to hold, and manim's
# Scene.wait(0) raises; VoiceoverScene.safe_wait skips a hold of one frame or less.
HOLD_FOR_REST_OF_SPEECH = (
    f"self.safe_wait(tracker.duration - min({VOICEOVER_VISUAL_CAP_S}, tracker.duration))"
)


def _hold_after(play):
    """The hold expected right after ``play``, inside the same voiceover block."""
    return play[: len(play) - len(play.lstrip())] + HOLD_FOR_REST_OF_SPEECH


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

    def test_example_axes_are_numbered(self):
        # The caption's "cap 24 from 12 lines up" must be readable off the axes.
        self.assertEqual(self.src.count("axes.x_axis.add_labels("), 3)
        self.assertEqual(self.src.count("axes.y_axis.add_labels("), 3)
        self.assertIn("(12.0, '12')", self.src)
        self.assertIn("(24.0, '24')", self.src)

    def test_example_budget_bends_are_sharp(self):
        self.assertIn(
            "min(24, max(5, 2 * x)), x_range=[0, 16, 0.04], use_smoothing=False", self.src
        )

    def test_example_marks_each_bound_while_it_is_narrated(self):
        # floor-and-cap: CAP is ann0, FLOOR is ann1. Each line draws with its own region's
        # narration, so the CAP no longer appears while the voice is on the FLOOR.
        floor_play = _play_after(self.src, "a FLOOR on the budget")
        cap_play = _play_after(self.src, "stops at a CAP")
        self.assertIn("Create(ann1)", floor_play)
        self.assertNotIn("Create(ann0)", floor_play)
        self.assertIn("Create(ann0)", cap_play)

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

    # --- tick numbers (opt-in per axis) ---

    def test_axes_carry_no_tick_numbers_by_default(self):
        sb = self._plot_beat(spec={"x": {"min": 0, "max": 16}, "y": {"min": 0, "max": 48}})
        src = generate_scene(sb)
        self.assertIn("'include_numbers': False", src)
        self.assertNotIn("add_labels(", src)

    def test_x_numbers_label_every_tick(self):
        sb = self._plot_beat(spec={"x": {"min": 0, "max": 16, "numbers": True}})
        src = generate_scene(sb)
        compile(src, "<generated>", "exec")
        line = _line_with(src, "axes.x_axis.add_labels(")
        for tick in ("(0.0, '0')", "(4.0, '4')", "(8.0, '8')", "(12.0, '12')", "(16.0, '16')"):
            self.assertIn(tick, line)
        self.assertNotIn("axes.y_axis.add_labels(", src)

    def test_y_numbers_label_every_tick(self):
        sb = self._plot_beat(spec={"y": {"min": 0, "max": 48, "numbers": True}})
        src = generate_scene(sb)
        line = _line_with(src, "axes.y_axis.add_labels(")
        self.assertIn("(24.0, '24')", line)
        self.assertIn("(48.0, '48')", line)
        self.assertNotIn("axes.x_axis.add_labels(", src)

    def test_fractional_ticks_keep_their_decimals(self):
        sb = self._plot_beat(spec={"x": {"min": 0, "max": 10, "numbers": True}})
        line = _line_with(generate_scene(sb), "axes.x_axis.add_labels(")
        self.assertIn("(2.5, '2.5')", line)
        self.assertIn("(10.0, '10')", line)

    def test_a_tick_rounding_to_zero_reads_zero_not_minus_zero(self):
        # -0.9 + 3 * 0.3 is -5.6e-17: it must label as 0, not -0.
        sb = self._plot_beat(spec={"x": {"min": -0.9, "max": 0.3, "numbers": True}})
        line = _line_with(generate_scene(sb), "axes.x_axis.add_labels(")
        self.assertIn("(0.0, '0')", line)
        self.assertNotIn("'-0'", line)

    def test_tick_numbers_are_written_out_in_full(self):
        # No scientific notation and no 6-significant-digit rounding.
        big = self._plot_beat(spec={"y": {"min": 0, "max": 2000000, "numbers": True}})
        line = _line_with(generate_scene(big), "axes.y_axis.add_labels(")
        self.assertIn("(1500000.0, '1500000')", line)
        self.assertNotIn("e+", line)
        fine = self._plot_beat(spec={"x": {"min": 0, "max": 100001, "numbers": True}})
        line = _line_with(generate_scene(fine), "axes.x_axis.add_labels(")
        self.assertIn("(25000.25, '25000.25')", line)

    def test_zero_label_is_skipped_where_the_other_axis_crosses_it(self):
        # With both ranges spanning 0 the axes cross mid-plot, and each axis line would
        # run through the other's "0" (Manim skips 0 by default for the same reason).
        sb = self._plot_beat(
            spec={
                "x": {"min": -4, "max": 4, "numbers": True},
                "y": {"min": -2, "max": 6, "numbers": True},
            }
        )
        src = generate_scene(sb)
        x_line = _line_with(src, "axes.x_axis.add_labels(")
        y_line = _line_with(src, "axes.y_axis.add_labels(")
        self.assertIn("(-4.0, '-4')", x_line)
        self.assertNotIn("(0.0, '0')", x_line)
        self.assertIn("(-2.0, '-2')", y_line)
        self.assertNotIn("(0.0, '0')", y_line)

    def test_zero_labels_are_kept_at_a_corner_origin(self):
        sb = self._plot_beat(
            spec={"x": {"min": 0, "max": 16, "numbers": True}, "y": {"numbers": True}}
        )
        src = generate_scene(sb)
        self.assertIn("(0.0, '0')", _line_with(src, "axes.x_axis.add_labels("))
        self.assertIn("(0.0, '0')", _line_with(src, "axes.y_axis.add_labels("))

    def test_tick_numbers_are_sans_text_not_latex(self):
        # Numbers match the other plot labels (sans Text) and need no LaTeX install.
        sb = self._plot_beat(spec={"x": {"numbers": True}})
        line = _line_with(generate_scene(sb), "axes.x_axis.add_labels(")
        self.assertIn("Text(", line)
        self.assertIn("font='Arial'", line)
        self.assertIn("disable_ligatures=True", line)
        self.assertNotIn("Tex(", line)

    def test_tick_numbers_are_added_before_axis_labels_are_placed(self):
        # The axis titles are placed next_to the axis; adding the numbers first makes
        # them part of the axis, so the titles clear the numbers instead of covering them.
        sb = self._plot_beat(spec={"x": {"numbers": True}, "y": {"numbers": True}})
        src = generate_scene(sb)
        self.assertLess(src.index("axes.x_axis.add_labels("), src.index("x_label = Text("))
        self.assertLess(src.index("axes.y_axis.add_labels("), src.index("y_label = Text("))

    def test_x_number_row_comes_out_of_the_plot_height(self):
        # The x numbers add a row under the axis; shortening the axes by that row keeps a
        # numbered beat inside today's frame margins (the caption must not hit the edge).
        default = generate_scene(self._plot_beat(spec={}))
        numbered = generate_scene(self._plot_beat(spec={"x": {"numbers": True}}))
        y_only = generate_scene(self._plot_beat(spec={"y": {"numbers": True}}))
        self.assertIn("y_length=3.25,", default)
        self.assertIn(f"y_length={3.25 - X_NUMBER_ROW!r},", numbered)
        self.assertIn("y_length=3.25,", y_only)  # y numbers add width, not height

    def test_non_bool_numbers_raises(self):
        # "false" is a truthy string: guessing would silently number the axis.
        sb = self._plot_beat(spec={"x": {"numbers": "false"}})
        with self.assertRaises(CodegenError):
            generate_scene(sb)

    # --- sharp bends for piecewise curves ---

    def test_piecewise_curve_is_sampled_densely_without_smoothing(self):
        # Smoothing rounds the bends of min/max; dense straight segments keep them sharp.
        sb = self._plot_beat(
            spec={"x": {"min": 0, "max": 16}, "curves": [{"fn": "min(24, max(5, 2 * x))"}]}
        )
        line = _line_with(generate_scene(sb), "curve0 = axes.plot(")
        self.assertIn("x_range=[0, 16, 0.04]", line)
        self.assertIn("use_smoothing=False", line)

    def test_every_piecewise_construct_gets_sharp_sampling(self):
        for fn in ("max(0, x - 3)", "min(1, x)", "abs(x - 5)", "x % 4"):
            with self.subTest(fn=fn):
                sb = self._plot_beat(spec={"curves": [{"fn": fn}]})
                line = _line_with(generate_scene(sb), "curve0 = axes.plot(")
                self.assertIn("use_smoothing=False", line)

    def test_smooth_curve_keeps_default_smoothed_sampling(self):
        sb = self._plot_beat(
            spec={"x": {"min": 0, "max": 120}, "curves": [{"fn": "0.5 ** (x / 30)"}]}
        )
        src = generate_scene(sb)
        line = _line_with(src, "curve0 = axes.plot(")
        self.assertIn("x_range=[0, 120],", line)
        self.assertNotIn("use_smoothing", src)

    # --- annotations tied to a region ---

    def _two_region_callout(self):
        return CodeCallout(
            display="floor line\ncap line",
            language="text",
            cite="a.py:1",
            regions=(
                Region(lines=(1,), narration="Talk about the floor."),
                Region(lines=(2,), narration="Talk about the cap."),
            ),
        )

    def test_annotation_tied_to_a_region_draws_with_that_region(self):
        sb = self._plot_beat(
            self._two_region_callout(),
            spec={
                "annotations": [
                    {"kind": "hline", "y": 24, "label": "CAP", "region": 2},
                    {"kind": "hline", "y": 5, "label": "FLOOR", "region": 1},
                ]
            },
        )
        src = generate_scene(sb)
        compile(src, "<generated>", "exec")
        floor_play = _play_after(src, "Talk about the floor.")
        cap_play = _play_after(src, "Talk about the cap.")
        self.assertIn("Create(ann1)", floor_play)
        self.assertNotIn("Create(ann0)", floor_play)
        self.assertIn("Create(ann0)", cap_play)
        self.assertNotIn("Create(ann1)", cap_play)
        self.assertEqual(src.count("Create(ann0)"), 1)

    def test_untied_annotations_still_draw_with_the_first_region(self):
        sb = self._plot_beat(
            self._two_region_callout(),
            spec={
                "annotations": [
                    {"kind": "hline", "y": 24, "label": "CAP"},
                    {"kind": "hline", "y": 5, "label": "FLOOR", "region": 2},
                ]
            },
        )
        src = generate_scene(sb)
        self.assertIn("Create(ann0)", _play_after(src, "Talk about the floor."))
        self.assertIn("Create(ann1)", _play_after(src, "Talk about the cap."))

    def test_annotation_tied_to_a_silent_region_draws_with_its_highlight(self):
        # A highlight-only region is a valid target: the line lands with its silent play.
        sb = self._plot_beat(
            CodeCallout(
                display="floor line\ncap line",
                language="text",
                cite="a.py:1",
                regions=(
                    Region(lines=(1,), narration="Talk about the floor."),
                    Region(lines=(2,), narration=""),
                ),
            ),
            spec={"annotations": [{"kind": "hline", "y": 24, "label": "CAP", "region": 2}]},
        )
        src = generate_scene(sb)
        compile(src, "<generated>", "exec")
        self.assertNotIn("Create(ann0)", _play_after(src, "Talk about the floor."))
        self.assertIn("Create(ann0)", _line_with(src, "FadeIn(box1)"))

    def test_annotation_region_out_of_range_raises(self):
        for region in (0, 3, True, "2"):
            with self.subTest(region=region):
                sb = self._plot_beat(
                    self._two_region_callout(),
                    spec={"annotations": [{"kind": "hline", "y": 1, "region": region}]},
                )
                with self.assertRaises(CodegenError):
                    generate_scene(sb)

    def test_annotation_region_without_regions_raises(self):
        # No regions means no region narration to wait for: fail, never drop it silently.
        sb = self._plot_beat(spec={"annotations": [{"kind": "hline", "y": 1, "region": 1}]})
        with self.assertRaises(CodegenError):
            generate_scene(sb)

    # --- voiced plot plays: capped, then hold for the rest of the speech ---

    def test_voiced_region_play_is_capped_then_holds_for_its_narration(self):
        # A tied annotation's dashed line draws dash by dash before its label. Stretched
        # over the whole region narration, the label landed only as the voice finished.
        sb = self._plot_beat(
            self._two_region_callout(),
            spec={
                "curves": [{"fn": "x"}, {"fn": "2 * x"}, {"fn": "3 * x"}],
                "annotations": [
                    {"kind": "hline", "y": 24, "label": "CAP", "region": 2},
                    {"kind": "hline", "y": 5, "label": "FLOOR", "region": 1},
                ],
            },
        )
        src = generate_scene(sb)
        compile(src, "<generated>", "exec")
        for narration, visuals in (
            ("Talk about the floor.", ("Create(box0)", "Create(curve1)", "Create(ann1)")),
            ("Talk about the cap.", ("FadeIn(box1)", "Create(curve2)", "Create(ann0)")),
        ):
            with self.subTest(narration=narration):
                play = _play_after(src, narration)
                for visual in visuals:
                    self.assertIn(visual, play)
                self.assertTrue(play.endswith(CAPPED_RUN_TIME), play)
                self.assertEqual(_line_after(src, play), _hold_after(play))
        self.assertNotIn("run_time=tracker.duration)", src)

    def test_voiced_region_on_a_code_canvas_is_capped_too(self):
        # Code-only beats share the region loop, so their highlight box draws the same way.
        sb = Storyboard(
            version=1,
            title="t",
            source=Source(repo_root="/tmp"),
            theme=Theme(),
            beats=(
                Beat(
                    id="b",
                    canvas=Canvas(kind="code", spec={}),
                    narration="n",
                    code_callout=self._two_region_callout(),
                ),
            ),
        )
        src = generate_scene(sb)
        play = _play_after(src, "Talk about the floor.")
        self.assertIn("Create(box0)", play)
        self.assertTrue(play.endswith(CAPPED_RUN_TIME), play)
        self.assertEqual(_line_after(src, play), _hold_after(play))

    def test_silent_region_play_keeps_its_fixed_run_time(self):
        # No narration to hold for: the highlight plays for a fixed second, then pauses.
        sb = self._plot_beat(
            CodeCallout(
                display="floor line\ncap line",
                language="text",
                cite="a.py:1",
                regions=(Region(lines=(1,), narration=""),),
            ),
            spec={"annotations": [{"kind": "hline", "y": 5, "label": "FLOOR"}]},
        )
        src = generate_scene(sb)
        play = _line_with(src, "Create(box0)")
        self.assertEqual(play, "        self.play(Create(box0), Create(ann0), run_time=1.0)")
        self.assertEqual(_line_after(src, play), "        self.wait(0.6)")

    def test_beat_narration_draws_the_first_curve_capped_then_holds(self):
        # The opening narration describes the curve: show its whole shape while it is
        # described, not a line still crawling across the axes as the voice ends.
        sb = self._plot_beat(spec={"curves": [{"fn": "min(24, max(5, 2 * x))"}]})
        src = generate_scene(sb)
        play = _play_after(src, "with self.voiceover(text='n')")
        self.assertEqual(play.strip(), f"self.play(Create(curve0), {CAPPED_RUN_TIME}")
        self.assertEqual(_line_after(src, play), _hold_after(play))

    def test_example_plot_beats_never_stretch_a_play_over_a_voiceover(self):
        self.assertNotIn("run_time=tracker.duration", self.src)
        self.assertTrue(_play_after(self.src, "stops at a CAP").endswith(CAPPED_RUN_TIME))

    def test_every_capped_play_holds_through_safe_wait(self):
        # A voiced line of 0.75 s or less would emit wait(0) and kill the whole render.
        self.assertEqual(self.src.count(CAPPED_RUN_TIME), 6)
        self.assertEqual(self.src.count(HOLD_FOR_REST_OF_SPEECH), 6)
        self.assertNotIn("self.wait(max(0, tracker.duration", self.src)

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
        self.assertIn(HOLD_FOR_REST_OF_SPEECH, src)
        # Old stretchy path must be gone from reveal steps.
        self.assertNotIn("run_time=tracker.duration)", src)


if __name__ == "__main__":
    unittest.main()
