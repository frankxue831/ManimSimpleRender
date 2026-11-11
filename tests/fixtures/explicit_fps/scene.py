"""Public, silent geometry fixture with no source FPS or resolution override."""

from manim import BLUE, RED, Scene, Square, Text
from scene_production.frame_clock import hold_until_frame


class ExplicitFps(Scene):
    def construct(self):
        # scene:start context
        self.next_section("context")
        label = Text("Blue square", font_size=36).shift([0, 2, 0])
        square = Square(side_length=2, color=BLUE, fill_opacity=1)
        self.add(label, square)
        hold_until_frame(self, round(self.camera.frame_rate / 5))
        # scene:end context
        # scene:start ending
        self.next_section("ending")
        self.remove(label)
        label = Text("Red square", font_size=36).shift([0, 2, 0])
        self.add(label)
        square.set_color(RED)
        hold_until_frame(self, round(self.camera.frame_rate * 2 / 5))
        # scene:end ending
