from manim import *
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[2] / "assets"  # repo-root assets/

# Helper methods (can be part of a base class or duplicated if simple enough)
def _arrow_with_label(scene, start, end, txt, color=WHITE, is_curve=False, curve_angle=PI/6):
    """Return VGroup(arrow,label) ready to be added into a frame."""
    if is_curve:
        arr = CurvedArrow(start, end, angle=curve_angle, color=color,
                          stroke_width=4, z_index=2)
    else:
        arr = Arrow(start, end, buff=0.15, stroke_width=4,
                    color=color, z_index=2)
    lbl = Text(txt, font="Arial", font_size=22, z_index=3)\
          .next_to(arr.get_center(), UP, buff=0.25)
    return VGroup(arr, lbl)

def _create_check_icon(scene, position):
    """Create a check icon with fallback if SVG fails."""
    try:
        icon = SVGMobject(ASSETS / "green_check.svg").scale(0.4)\
               .move_to(position).set_z_index(4)
        return icon
    except (FileNotFoundError, Exception):
        check = VGroup()
        circle = Circle(radius=0.2, color=GREEN, fill_opacity=1, stroke_width=2)
        check_mark = Text("✓", font="Arial", font_size=24, color=WHITE).move_to(circle.get_center())
        check.add(circle, check_mark)
        return check.move_to(position).set_z_index(4)

class ProxyScenarioBase(Scene):
    """Base setup for proxy pattern scenes."""
    def _setup_static_stage(self):
        X_L, X_C, X_R, Y = -5, 0, 5, 1
        self.client = Circle(color=BLUE).scale(1).move_to([X_L, Y, 0])
        self.proxy_shape = Square(color=ORANGE).scale(1).move_to([X_C, Y, 0]) # Renamed to avoid conflict
        self.real_subject_shape = Square(color=GREEN).scale(1).move_to([X_R, Y, 0]) # Renamed

        client_lbl = Text("Client", font="Arial", font_size=24).next_to(self.client, DOWN)
        proxy_lbl  = Text("Proxy",  font="Arial", font_size=24).next_to(self.proxy_shape,  DOWN)
        real_lbl   = Text("RealSubject", font="Arial", font_size=24).next_to(self.real_subject_shape, DOWN)

        self.play(FadeIn(self.client), Write(client_lbl))
        self.play(FadeIn(self.proxy_shape),  Write(proxy_lbl))
        self.play(FadeIn(self.real_subject_shape),   Write(real_lbl))
        self.wait()

        self.iface_box = SurroundingRectangle(VGroup(self.proxy_shape, self.real_subject_shape), buff=0.6, color=BLUE, z_index=1)
        iface_lbl = Text("Subject Interface", font="Arial", font_size=24).next_to(self.iface_box, UP)
        self.play(Create(self.iface_box), Write(iface_lbl))
        self.wait()

    # Make helpers available to subclasses that need them
    def arrow_with_label(self, start, end, txt, color=WHITE, is_curve=False, curve_angle=PI/6):
        return _arrow_with_label(self, start, end, txt, color, is_curve, curve_angle)

    def create_check_icon(self, position):
        return _create_check_icon(self, position)


class ProxyScenario1Scene(ProxyScenarioBase):
    """Illustrates Proxy Pattern: Scenario 1 - Direct pass-through."""
    def construct(self):
        self._setup_static_stage()

        title = Text("Scenario 1 -- Direct pass-through", font="Arial", font_size=30)\
                .to_edge(UP, buff=0.3)
        self.play(Write(title))
        self.wait(0.5)

        # beat 1 : client ➜ proxy
        frame = VGroup()
        frame += self.arrow_with_label(self.client.get_right(), self.proxy_shape.get_left(),
                                       "doSomething()")
        self.play(Create(frame))
        self.wait(1.0)
        self.play(FadeOut(frame))

        # beat 2a : checkPermission()
        tag = Text("checkPermission()", font="Arial", font_size=22, color=YELLOW)\
              .next_to(self.proxy_shape, UP, buff=0.15)
        self.play(FadeIn(tag))
        self.wait(1.0)
        self.play(FadeOut(tag))

        # beat 2b : proxy ➜ real
        frame = VGroup()
        frame += self.arrow_with_label(self.proxy_shape.get_right(), self.real_subject_shape.get_left(),
                                       "real.doSomething()")
        self.play(Create(frame))
        self.wait(1.0)
        self.play(FadeOut(frame))

        # beat 3a : real ➜ proxy (result)
        frame = VGroup()
        frame += self.arrow_with_label(self.real_subject_shape.get_left(), self.proxy_shape.get_right(),
                                       "Result", color=GREEN, is_curve=True)
        self.play(Create(frame))
        self.wait(1.0)
        self.play(FadeOut(frame))

        # beat 3b : proxy ➜ client (result)
        frame = VGroup()
        frame += self.arrow_with_label(self.proxy_shape.get_left(), self.client.get_right(),
                                       "Result", color=GREEN, is_curve=True, curve_angle=-PI/3)
        ok_icon = self.create_check_icon(frame.get_center() + DOWN * 0.5)
        frame.add(ok_icon)
        self.play(Create(frame))
        self.wait(1.5)
        self.play(FadeOut(frame), FadeOut(title))
        self.wait(1) # Wait a bit at the end of the scene


class ProxyScenario2Scene(ProxyScenarioBase):
    """Illustrates Proxy Pattern: Scenario 2 - Proxy caching, subsequent call, and recap."""
    def construct(self):
        self._setup_static_stage()

        title = Text("Scenario 2 -- Proxy caching", font="Arial", font_size=30)\
                .to_edge(UP, buff=0.3)
        self.play(Write(title))
        self.wait(0.5)

        # beat 1 : client ➜ proxy
        frame = self.arrow_with_label(self.client.get_right(), self.proxy_shape.get_left(),
                                      "doSomething()")
        self.play(Create(frame))
        self.wait(1.0)
        self.play(FadeOut(frame))

        # beat 2 : accessControl()
        tag = Text("accessControl()", font="Arial", font_size=22, color=YELLOW)\
              .next_to(self.proxy_shape, UP, buff=0.15)
        self.play(FadeIn(tag))
        self.wait(1.0)
        self.play(FadeOut(tag))

        # beat 3a : checkCache()
        tag = Text("checkCache()", font="Arial", font_size=22, color=YELLOW)\
              .next_to(self.proxy_shape, UP, buff=0.15)
        self.play(FadeIn(tag))
        self.wait(1.0)
        self.play(FadeOut(tag))

        # beat 3b : proxy ➜ real (cache miss)
        frame = self.arrow_with_label(self.proxy_shape.get_right(), self.real_subject_shape.get_left(),
                                      "real.doSomething()")
        self.play(Create(frame))
        self.wait(1.0)
        self.play(FadeOut(frame))

        # beat 4 : real ➜ proxy (raw result)
        frame = self.arrow_with_label(self.real_subject_shape.get_left(), self.proxy_shape.get_right(),
                                      "Raw result", color=GREEN, is_curve=True)
        self.play(Create(frame))
        self.wait(1.0)
        self.play(FadeOut(frame))

        # beat 5 : cacheResult()
        tag = Text("cacheResult()", font="Arial", font_size=22, color=YELLOW)\
              .next_to(self.proxy_shape, UP, buff=0.15)
        self.play(FadeIn(tag))
        self.wait(1.0)
        self.play(FadeOut(tag))

        # beat 6 : proxy ➜ client (processed result)
        frame = self.arrow_with_label(self.proxy_shape.get_left(), self.client.get_right(),
                                      "Processed result",
                                      color=GREEN, is_curve=True, curve_angle=-PI/3)
        ok_icon = self.create_check_icon(frame.get_center() + DOWN * 0.5)
        frame.add(ok_icon)
        self.play(Create(frame))
        self.wait(1.5)
        self.play(FadeOut(frame), FadeOut(title))

        # ---------- SUBSEQUENT CACHED CALL ---------------------------------
        sub_title = Text("Subsequent call (cached)", font="Arial", font_size=24)\
              .next_to(self.iface_box, DOWN, buff=0.8) # Use self.iface_box
        self.play(Write(sub_title))
        self.wait(0.5)

        frame = self.arrow_with_label(self.client.get_right(), self.proxy_shape.get_left(), # Use self.client, self.proxy_shape
                                      "doSomething()")
        cached_tag = Text("fromCache()", font="Arial", font_size=22, color=GREEN)\
                     .next_to(self.proxy_shape, UP, buff=0.15) # Use self.proxy_shape
        repeat_lbl = Text("Same Request Again", font="Arial", font_size=22, color=BLUE)\
                     .next_to(frame, DOWN, buff=0.1).set_z_index(4)
        self.play(Create(frame), Write(repeat_lbl))
        self.play(FadeIn(cached_tag))
        self.wait(1.0)
        self.play(FadeOut(frame), FadeOut(repeat_lbl))

        frame = self.arrow_with_label(self.proxy_shape.get_left(), self.client.get_right(), # Use self.proxy_shape, self.client
                                      "Cached result",
                                      color=YELLOW, is_curve=True, curve_angle=-PI/3)
        ok_icon = self.create_check_icon(frame.get_center() + DOWN * 0.5)
        frame.add(ok_icon)
        self.play(Create(frame))
        self.wait(1.2)
        self.play(FadeOut(frame), FadeOut(cached_tag), FadeOut(sub_title))

        # ---------- RECAP ---------------------------------------------------
        recap = Text("Proxy enforces access *and* caches results.", font="Arial",
                     font_size=24).to_edge(DOWN, buff=0.3)
        self.play(Write(recap))
        self.wait(4)
        self.clear()
