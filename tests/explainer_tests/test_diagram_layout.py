"""Tests for the pure diagram layout helpers (no manim import)."""

import unittest

from explainer.storyboard.diagram_layout import (
    LayoutError,
    bow_control_points,
    check_layout,
    resolve_anchor,
)


class ResolveAnchorTests(unittest.TestCase):
    def test_known_anchor_maps_to_coordinates(self):
        self.assertEqual(resolve_anchor("center"), (0.0, 0.0))
        self.assertEqual(resolve_anchor("left"), (-5.0, 0.0))
        self.assertEqual(resolve_anchor("right"), (5.0, 0.0))

    def test_unknown_anchor_raises(self):
        with self.assertRaises(LayoutError):
            resolve_anchor("nowhere")


class BowControlPointsTests(unittest.TestCase):
    def test_down_bow_dips_below_the_midline(self):
        c1, c2 = bow_control_points((-5.0, 0.0), (5.0, 0.0), "down")
        self.assertLess(c1[1], 0.0)
        self.assertLess(c2[1], 0.0)

    def test_up_bow_rises_above_the_midline(self):
        c1, c2 = bow_control_points((-5.0, 0.0), (5.0, 0.0), "up")
        self.assertGreater(c1[1], 0.0)
        self.assertGreater(c2[1], 0.0)


class CheckLayoutTests(unittest.TestCase):
    def test_sane_scene_passes(self):
        check_layout([("agent", (-5.0, 0.0)), ("goal", (5.0, 0.0))])

    def test_off_frame_point_raises(self):
        with self.assertRaises(LayoutError):
            check_layout([("x", (9.0, 0.0))])

    def test_overlapping_points_raise(self):
        with self.assertRaises(LayoutError):
            check_layout([("a", (0.0, 0.0)), ("b", (0.1, 0.0))])


if __name__ == "__main__":
    unittest.main()
