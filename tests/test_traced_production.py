"""Optional trace identity participates in render and original-video reinspection."""

import json
from pathlib import Path
import unittest
from unittest.mock import patch
import tests.test_scene_production as fixtures
from scene_production import render, digest
from scene_production.frame_trace import FrameTrace
from scene_production.reinspection import reinspect


class TracedProductionTests(unittest.TestCase):
    def setUp(self):
        fixtures.PipelineTests.setUp(self)

    @staticmethod
    def fake_tool(command, timeout, *, cwd=None):
        out = fixtures.PipelineTests.fake_tool(command, timeout, cwd=cwd)
        if command[0] == "ffprobe":
            d = json.loads(out)
            d["streams"][0].update(nb_frames="30", r_frame_rate="30/1")
            return json.dumps(d).encode()
        return out

    def runner(self, mode=None):
        base = fixtures.PipelineTests.runner(self)
        outer = self

        class Runner:
            def run_job(self, job, command, event, on_log, env):
                result = base.run_job(job, command, event, on_log, env)
                outer.assertEqual(command[1], "-I")
                outer.assertEqual(Path(command[2]).name, "launcher.py")
                outer.assertEqual(command[3], "capture")
                c = FrameTrace("30/1", digest(outer.source))
                c.section("one")
                c.add_frame(bytes(16 * 16 * 4), (16, 16, 4), 29 if mode == "count" else 30)
                Path(env["SCENE_PRODUCTION_FRAME_TRACE"]).write_text(json.dumps(c.finish()))
                return result

        return Runner()

    def test_traced_render_and_reinspect_preserve_identity(self):
        with patch("scene_production.tool", side_effect=self.fake_tool):
            r = render(
                self.source,
                "Demo",
                self.timeline,
                self.output,
                runner=self.runner(),
                record_scene_frames=True,
            )
            p = Path(r["run_dir"]) / "result.json"
            checked = reinspect(p, self.timeline, self.root / "rechecked")
        self.assertEqual(r["frame_trace"]["sha256"], checked["frame_trace"]["sha256"])
        self.assertNotEqual(r["frame_trace"]["path"], checked["frame_trace"]["path"])
        self.assertEqual(digest(checked["frame_trace"]["path"]), r["frame_trace"]["sha256"])

    def test_count_mismatch_fails_without_success_and_tampering_is_rejected(self):
        with patch("scene_production.tool", side_effect=self.fake_tool):
            with self.assertRaisesRegex(ValueError, "frame count"):
                render(
                    self.source,
                    "Demo",
                    self.timeline,
                    self.output,
                    runner=self.runner("count"),
                    record_scene_frames=True,
                )
            self.assertEqual(
                json.loads(next(self.output.glob("*/result.json")).read_text())["status"], "failed"
            )
            r = render(
                self.source,
                "Demo",
                self.timeline,
                self.root / "ok",
                runner=self.runner(),
                record_scene_frames=True,
            )
            Path(r["frame_trace"]["path"]).write_text("{}")
            with self.assertRaises(ValueError):
                reinspect(Path(r["run_dir"]) / "result.json", self.timeline, self.root / "bad")

    def test_traced_previews_select_exact_native_frame_indices(self):
        calls = []

        def observe(command, timeout, *, cwd=None):
            calls.append(command)
            return self.fake_tool(command, timeout, cwd=cwd)

        with patch("scene_production.tool", side_effect=observe):
            out = render(
                self.source,
                "Demo",
                self.timeline,
                self.output,
                runner=self.runner(),
                record_scene_frames=True,
            )
        self.assertEqual([v["native_frame_index"] for v in out["frames"]], [3, 27])
        selections = [c[c.index("-vf") + 1] for c in calls if "-vf" in c]
        self.assertEqual(selections, [r"select=eq(n\,3)", r"select=eq(n\,27)"])

    def test_explicit_fractional_frame_time_is_not_one_frame_late(self):
        from scene_production import _preview_frame_index

        for rate, count in [(24, 31), (60, 7), (30, 1)]:
            self.assertEqual(
                _preview_frame_index(count / rate, {"frame_rate": str(rate), "frame_count": 100}),
                count,
            )
            self.assertEqual(
                _preview_frame_index(
                    (count + 0.001) / rate, {"frame_rate": str(rate), "frame_count": 100}
                ),
                count + 1,
            )
