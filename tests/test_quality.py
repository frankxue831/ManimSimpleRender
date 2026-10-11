"""Tests for app.render.quality -- the canonical manim quality ladder.

Pure, Qt-free. Run: python3 -m unittest tests.test_quality
"""

import unittest

from app.render.quality import (
    QUALITY_LADDER,
    known_quality_flags,
    quality_flag_for_height,
)


class QualityLadderTests(unittest.TestCase):
    def test_ladder_matches_manim_0_20(self):
        self.assertEqual(
            QUALITY_LADDER,
            (("-ql", 480), ("-qm", 720), ("-qh", 1080), ("-qp", 1440), ("-qk", 2160)),
        )

    def test_flag_for_each_known_height(self):
        self.assertEqual(quality_flag_for_height(480), "-ql")
        self.assertEqual(quality_flag_for_height(720), "-qm")
        self.assertEqual(quality_flag_for_height(1080), "-qh")
        self.assertEqual(quality_flag_for_height(1440), "-qp")
        self.assertEqual(quality_flag_for_height(2160), "-qk")

    def test_unknown_height_falls_back(self):
        self.assertEqual(quality_flag_for_height(999), "-qm")
        self.assertEqual(quality_flag_for_height(999, fallback="-qh"), "-qh")

    def test_known_quality_flags(self):
        self.assertEqual(known_quality_flags(), {"-ql", "-qm", "-qh", "-qp", "-qk"})
