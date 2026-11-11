"""Public silent fixture, deliberately missing a lookup cue in the last section."""

from manim import BLUE, WHITE, Rectangle, Scene, Text

from scene_production.frame_clock import hold_until_frame


class SilentReview(Scene):
    def construct(self):
        self.camera.background_color = "#101923"
        # scene:start context
        self.next_section("context")
        self.add(Text("A lookup selects one row.", font_size=34, color=WHITE))
        hold_until_frame(self, 15)
        # scene:end context
        # scene:start lookup
        self.next_section("lookup")
        self.clear()
        self.add(Text("Result: row 2", font_size=34, color=WHITE).move_to([0, 2, 0]))
        for y in (0.6, -0.6):
            self.add(Rectangle(width=3, height=0.8, color=BLUE).move_to([0, y, 0]))
        hold_until_frame(self, 30)
        # scene:end lookup
