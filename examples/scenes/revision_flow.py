from manim import BLUE, LEFT, RIGHT, WHITE, Dot, Scene, Text, Transform
from scene_font import scene_font
from scene_production.frame_clock import hold_until_frame, frame_count

FONT = scene_font(
    "脚本由助手编写工具执行并检查改动画要重新渲染只改检查时刻复用原视频并核对身份加长字幕需要更多阅读时间后续对象保持同一个蓝点开始结束"
)


def label(value):
    obj = Text(value, font=FONT, font_size=30, color=WHITE)
    if obj.width > 12:
        obj.scale_to_fit_width(12)
    return obj.move_to([0, 2.5, 0])


class RevisionFlow(Scene):
    def construct(self):
        self.camera.background_color = "#101923"
        # scene:start setup
        self.next_section("setup")
        cap = label("脚本由助手编写；工具执行并检查")
        dot = Dot([-4, 0, 0], color=BLUE, radius=0.25)
        self.add(cap, dot)
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
