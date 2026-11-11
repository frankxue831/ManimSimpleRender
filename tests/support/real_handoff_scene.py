"""Public two-second geometric fixture; no fonts, assets, audio or network."""

from manim import BLUE, RIGHT, YELLOW, Circle, Dot, Scene, config, linear

config.notify_outdated_version = False


class HandoffFixture(Scene):
    def construct(self):
        dot = Dot([-2, 0, 0], radius=0.2, color=BLUE)
        target = Circle(radius=0.5, color=YELLOW).move_to([2, 0, 0])
        self.add(dot, target)
        self.wait(0.4)
        self.play(dot.animate.shift(4 * RIGHT), run_time=1, rate_func=linear)
        self.wait(0.6)
