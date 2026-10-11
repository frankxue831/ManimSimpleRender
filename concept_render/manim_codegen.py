"""Generate a standalone scene with literal data and fixed rendering logic."""

from .model import validate

_SCENE = """from manim import (
    Scene, Text, RoundedRectangle, VGroup, Arrow, CurvedArrow,
    FadeIn, Transform, AnimationGroup, Succession, config, PI,
)

config.frame_width = 128 / 9
config.frame_height = 8

DATA = __DATA_LITERAL__


class ConceptScene(Scene):
    def construct(self):
        # Positions describe a schematic, not a quantitative forecast.
        self.camera.background_color = "#0B1426"
        light = "#EDF4FC"
        cyan = "#38CFE4"
        amber = "#FFBF47"

        def fitted(text, size, width, height):
            result = Text(text, font=DATA["font"], font_size=size, color=light)
            factor = min(1, width / max(result.width, 0.001),
                         height / max(result.height, 0.001))
            result.scale(factor)
            return result

        title = fitted(DATA["title"], 34, 13, 0.42)
        title.move_to([0, 3.35, 0]).set_z_index(3)
        self.add(title)
        nodes = {}
        boxes = {}
        for node in DATA["nodes"]:
            box = RoundedRectangle(
                width=3.1, height=0.9, corner_radius=0.16,
                stroke_color=cyan, stroke_width=2,
                fill_color="#13283E", fill_opacity=1,
            )
            label = fitted(node["label"], 27, 2.8, 0.58)
            group = VGroup(box, label)
            group.move_to([node["x"], node["y"], 0]).set_z_index(1)
            # Child layers survive regrouping during later box animations.
            box.set_z_index(1)
            label.set_z_index(2)
            nodes[node["id"]] = group
            boxes[node["id"]] = box

        visible = set()
        revealed_edges = set()
        caption = None
        for step in DATA["steps"]:
            animations = []
            for identifier in step["show"]:
                animations.append(FadeIn(nodes[identifier]))
                visible.add(identifier)

            for index, edge in enumerate(DATA["edges"]):
                start, end = edge["from"], edge["to"]
                if index in revealed_edges or not {start, end} <= visible:
                    continue
                first, last = boxes[start], boxes[end]
                direction = last.get_center() - first.get_center()
                distance = sum(float(v) ** 2 for v in direction) ** 0.5
                if distance > 0.001:
                    # Opposing edge centers avoid corner-biased endpoints.
                    if abs(direction[0]) >= abs(direction[1]):
                        if direction[0] > 0:
                            a, b = first.get_right(), last.get_left()
                        else:
                            a, b = first.get_left(), last.get_right()
                    elif direction[1] > 0:
                        a, b = first.get_top(), last.get_bottom()
                    else:
                        a, b = first.get_bottom(), last.get_top()
                    direction = direction / distance
                    gap = sum(float(v) * float(w) for v, w in zip(b - a, direction))
                else:
                    gap = 0
                if gap > 0.15:
                    arrow = Arrow(a, b, buff=0.04, color=cyan, stroke_width=2)
                else:
                    # Route close or coincident endpoints around the boxes.
                    a = first.get_right()
                    b = last.get_top()
                    if sum(float(v) ** 2 for v in b - a) < 0.01:
                        b = last.get_bottom()
                    arrow = CurvedArrow(a, b, angle=PI, color=cyan, stroke_width=2)
                arrow.set_z_index(0)
                animations.append(FadeIn(arrow))
                revealed_edges.add(index)

            next_caption = fitted(step["caption"], 27, 13, 0.4)
            next_caption.move_to([0, -3.2, 0]).set_z_index(3)
            changes = []
            if caption is None:
                caption = next_caption
                changes.append(FadeIn(caption))
            else:
                changes.append(Transform(caption, next_caption))
            for identifier in nodes:
                if identifier in visible:
                    color = amber if identifier in step["highlight"] else cyan
                    changes.append(boxes[identifier].animate.set_stroke(color=color))
            animations.append(AnimationGroup(*changes, lag_ratio=0))
            # One timed transition includes every reveal and caption change.
            # The title is added without consuming separate scene time.
            transition = 0.6
            self.play(Succession(*animations), run_time=transition)
            self.wait(step["duration"] - transition)
"""


def generate_scene(data: dict) -> str:
    """Validate and embed only safe Python literals in fixed scene code."""
    validate(data)
    return _SCENE.replace("__DATA_LITERAL__", ascii(data), 1)
