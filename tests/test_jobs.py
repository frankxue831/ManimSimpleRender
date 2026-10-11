"""Tests for app.render.jobs.build_jobs (explicit (file, scene) job list). Batch 3.

build_jobs takes identity-carrying DiscoveredScene objects (Condition 2), so the
file x scene cross-product (legacy render.py:34-49) and the duplicate-name
collision are both impossible by construction.
Run: python3 -m unittest tests.test_jobs
"""

import os
import unittest

from app.render.jobs import RenderJob, build_jobs, media_output_collisions
from app.scenes.discovery import DiscoveredScene, discover_scenes

INPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "input")


class BuildJobsTests(unittest.TestCase):
    def test_one_job_per_selected_scene_bound_to_owning_file(self):
        selected = [
            DiscoveredScene(
                "input/proxy_pattern_scene.py", "ProxyScenario1Scene", "ProxyScenarioBase"
            ),
            DiscoveredScene("input/simple.py", "ImprovedMindMapAnimation07", "MovingCameraScene"),
        ]
        jobs = build_jobs(selected)
        self.assertEqual(
            jobs,
            [
                RenderJob("input/proxy_pattern_scene.py", "ProxyScenario1Scene"),
                RenderJob("input/simple.py", "ImprovedMindMapAnimation07"),
            ],
        )
        self.assertEqual(len(jobs), 2)  # total == #selected; no N-file inflation

    def test_empty_selection_yields_no_jobs(self):
        self.assertEqual(build_jobs([]), [])

    def test_duplicate_class_name_yields_two_distinct_jobs(self):
        scenes = discover_scenes(INPUT_DIR)
        dups = [s for s in scenes if s.scene_class == "TestDebuggingWorkflowDetailed"]
        self.assertEqual(len(dups), 2)
        jobs = build_jobs(dups)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(len({j.file_path for j in jobs}), 2)
        self.assertTrue(all(j.scene_class == "TestDebuggingWorkflowDetailed" for j in jobs))

    def test_identical_pairs_deduped(self):
        s = DiscoveredScene("input/simple.py", "ImprovedMindMapAnimation07", "MovingCameraScene")
        self.assertEqual(
            build_jobs([s, s]), [RenderJob("input/simple.py", "ImprovedMindMapAnimation07")]
        )


class MediaOutputCollisionTests(unittest.TestCase):
    """BL-05: two files with the same basename + scene class alias manim's output."""

    def test_equivalent_media_components_collide_conservatively(self):
        composed = "\u00e9"
        decomposed = "e\u0301"
        cases = {
            "same basename and scene": (
                RenderJob("a/scene.py", "Demo"),
                RenderJob("b/scene.py", "Demo"),
            ),
            "case-only basename difference": (
                RenderJob("a/scene.py", "Demo"),
                RenderJob("b/Scene.py", "Demo"),
            ),
            "case-only scene class difference": (
                RenderJob("a/scene.py", "Demo"),
                RenderJob("b/scene.py", "demo"),
            ),
            "NFC/NFD equivalent components": (
                RenderJob(f"a/sc{composed}ne.py", f"D{composed}mo"),
                RenderJob(f"b/sc{decomposed}ne.py", f"D{decomposed}mo"),
            ),
            # sharp-s (U+00DF) casefolds to "ss"; basename-only identity dimension
            "casefold-only basename difference": (
                RenderJob("a/stra\u00dfe.py", "Demo"),
                RenderJob("b/STRASSE.py", "Demo"),
            ),
        }
        for name, (first, second) in cases.items():
            with self.subTest(case=name):
                jobs = [first, second, RenderJob("c/other.py", "Other")]  # + decoy
                collisions = media_output_collisions(jobs)
                self.assertEqual(len(collisions), 1)
                self.assertEqual(len(collisions[0]), 2)
                self.assertCountEqual(collisions[0], jobs[:2])

    def test_different_basename_does_not_collide(self):
        jobs = build_jobs(
            [
                DiscoveredScene("a/one.py", "Demo", "Scene"),
                DiscoveredScene("b/two.py", "Demo", "Scene"),
            ]
        )
        self.assertEqual(media_output_collisions(jobs), [])

    def test_same_basename_different_scene_does_not_collide(self):
        jobs = build_jobs(
            [
                DiscoveredScene("a/scene.py", "AlphaScene", "Scene"),
                DiscoveredScene("b/scene.py", "BetaScene", "Scene"),
            ]
        )
        self.assertEqual(media_output_collisions(jobs), [])

    def test_partial_component_matches_do_not_collide(self):
        # Guards against grouping on normalized stem OR normalized class alone.
        jobs = [
            RenderJob("a/scene.py", "Alpha"),
            RenderJob("b/SCENE.py", "Beta"),
            RenderJob("c/one.py", "Demo"),
            RenderJob("d/two.py", "demo"),
        ]
        self.assertEqual(media_output_collisions(jobs), [])


if __name__ == "__main__":
    unittest.main()
