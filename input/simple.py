from manim import (
    MovingCameraScene, FadeIn, Create, ApplyMethod, Restore, Text, Ellipse, VGroup, Arrow, 
    BLUE, WHITE, smooth, rate_functions
)
import numpy as np

class ImprovedMindMapAnimation07(MovingCameraScene):
    """Animation class for creating a mind map of Microeconomics topics."""

    def construct(self):
        """Construct the mind map animation."""
        total_duration = 120  # Total duration for the animation in seconds
        intro_duration = 8     # Duration for the root node introduction
        child_intro_duration = 15  # Duration for each child node introduction (including line)
        outro_duration = 10    # Duration for the final overview
        child_count = 5        # Number of child nodes
        pause_between_children = 3  # Pause duration between introducing each child node
        pause_between_scenes = 2    # Pause duration between scenes

        # Scene 1: Introduction of Root Node
        root = self.create_root_node("Microeconomics Topics", BLUE)
        self.play(FadeIn(root, run_time=intro_duration))
        self.wait(pause_between_scenes)

        # Scene 2: Introduction of Child Nodes with different colors
        child_texts_and_colors = [
            ("Demand and Supply", "#00FF00"),  # GREEN
            ("Consumer Behavior", "#FFA500"),  # ORANGE
            ("Production and Costs", "#800080"),  # PURPLE
            ("Market Structures", "#008080"),  # TEAL
            ("Market Failures", "#FF0000")  # RED
        ]
        child_nodes = [
            self.create_ellipse_node(text, color)
            for text, color in child_texts_and_colors
        ]

        # Arrange child nodes in a circular layout around the root node
        self.arrange_nodes(root, child_nodes)

        # Add the root node and save the camera state
        self.add(root)
        self.camera.frame.save_state()

        # Draw child nodes with connecting lines and staggered introduction
        for child in child_nodes:
            line = self.create_arrow(root, child)
            self.play(
                FadeIn(child, run_time=child_intro_duration * 0.4),
                Create(line, run_time=child_intro_duration * 0.4),
                self.camera.frame.animate.move_to(
                    child.get_center()
                ).scale(1.2).set_run_time(child_intro_duration * 0.2).set_rate_func(smooth),
                ApplyMethod(child[0].set_opacity, 0.9)  # Enhanced highlighting effect
            )
            self.wait(pause_between_children)  # Pause between child nodes

        # Scene 3: Final Overview
        self.play(Restore(self.camera.frame, run_time=outro_duration))
        self.wait(outro_duration)

    def create_root_node(self, text, color):
        """Create root node with an ellipse shape and larger size."""
        text_mobject = Text(text, font_size=24, font="Arial").set_color(WHITE)
        ellipse = Ellipse(
            width=text_mobject.width + 1,
            height=text_mobject.height + 0.6
        ).set_fill(color, opacity=0.8).set_stroke(color)
        node = VGroup(ellipse, text_mobject.move_to(ellipse.get_center()))
        return node

    def create_ellipse_node(self, text, color):
        """Create child nodes with ellipse shapes and slightly smaller size."""
        text_mobject = Text(text, font_size=20, font="Arial").set_color(WHITE)
        ellipse = Ellipse(
            width=text_mobject.width + 0.8,
            height=text_mobject.height + 0.4
        ).set_fill(color, opacity=0.8).set_stroke(color)
        node = VGroup(ellipse, text_mobject.move_to(ellipse.get_center()))
        return node

    def create_arrow(self, start_node, end_node):
        """Create an arrow connecting the start and end nodes."""
        start_point = start_node.get_center()
        end_point = end_node.get_center()
        vector = end_point - start_point
        unit_vector = vector / np.linalg.norm(vector)
        start_ellipse_width = start_node[0].width / 2
        start_ellipse_height = start_node[0].height / 2
        end_ellipse_width = end_node[0].width / 2
        end_ellipse_height = end_node[0].height / 2
        start_angle = np.arctan2(
            unit_vector[1] * start_ellipse_width,
            unit_vector[0] * start_ellipse_height
        )
        end_angle = np.arctan2(
            -unit_vector[1] * end_ellipse_width,
            -unit_vector[0] * end_ellipse_height
        )
        start_point = start_point + np.array([
            np.cos(start_angle) * start_ellipse_width,
            np.sin(start_angle) * start_ellipse_height,
            0
        ])
        end_point = end_point + np.array([
            np.cos(end_angle) * end_ellipse_width,
            np.sin(end_angle) * end_ellipse_height,
            0
        ])
        return Arrow(start_point, end_point, buff=0.1, stroke_width=4, color=WHITE)

    def arrange_nodes(self, root, child_nodes):
        """Arrange child nodes in a circular pattern around the root node."""
        num_children = len(child_nodes)
        radius = 4  # Radius of the circular layout

        for i, child in enumerate(child_nodes):
            angle = i * (2 * np.pi / num_children) - np.pi / 2  # Starting from the top
            x = radius * np.cos(angle)
            y = radius * np.sin(angle)
            child.move_to(root.get_center() + np.array([x, y, 0]))

        # Adjust positions to prevent overlapping (optional)
        self.adjust_positions(root, child_nodes)

    def adjust_positions(self, root, child_nodes):
        """Adjust the positions of nodes to avoid overlap, if necessary."""
        for i, node in enumerate(child_nodes):
            text_width = node[1].width
            text_height = node[1].height
            left = node.get_center()[0] - text_width / 2
            right = node.get_center()[0] + text_width / 2
            top = node.get_center()[1] + text_height / 2
            bottom = node.get_center()[1] - text_height / 2

            for other_node in child_nodes[i + 1:]:
                other_left = other_node.get_center()[0] - other_node[1].width / 2
                other_right = other_node.get_center()[0] + other_node[1].width / 2
                other_top = other_node.get_center()[1] + other_node[1].height / 2
                other_bottom = other_node.get_center()[1] - other_node[1].height / 2

                if (left < other_right and right > other_left and
                        top > other_bottom and bottom < other_top):
                    direction = node.get_center() - root.get_center()
                    unit_direction = direction / np.linalg.norm(direction)
                    node.shift(unit_direction * 0.5)