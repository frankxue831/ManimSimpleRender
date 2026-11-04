"""Authored project for a receiving agent; not generated from a language model at runtime."""

from pathlib import Path
from manim import BLUE, LEFT, RIGHT, WHITE, Dot, Scene, Text, Transform, SVGMobject
from scene_font import scene_font
from scene_production.frame_clock import hold_until_frame, frame_count


def label(value):
    obj = Text(value, font=scene_font(value), font_size=30, color=WHITE)
    if obj.width > 12:
        obj.scale_to_fit_width(12)
    return obj.move_to([0, 2.5, 0])


class HandoffExample(Scene):
    def construct(self):
        self.camera.background_color = "#101923"
        # scene:start setup
        self.next_section("setup")
        cap = label("脚本由助手编写；工具执行并检查")
        dot = Dot([-4, 0, 0], color=BLUE, radius=0.25)
        icon = (
            SVGMobject(Path(__file__).parent / "assets/project.svg")
            .scale(0.3)
            .move_to([-6, -2.6, 0])
        )
        self.add(cap, dot, icon)
        self.play(dot.animate.shift(RIGHT * 2), run_time=1)
        hold_until_frame(self, 150)
        # scene:end setup
        # scene:start distinction
        self.next_section("distinction")
        self.play(Transform(cap, label("改动画要重新渲染")))
        self.play(dot.animate.shift(RIGHT * 4), run_time=2)
        hold_until_frame(self, 360)
        # scene:end distinction
        # scene:start tail
        self.next_section("tail")
        tail_start = frame_count(self)
        self.remove(cap)
        cap = label("后续对象保持同一个蓝点")
        self.add(cap)
        self.play(dot.animate.shift(LEFT * 2), run_time=1)
        hold_until_frame(self, tail_start + 150)
        # scene:end tail
