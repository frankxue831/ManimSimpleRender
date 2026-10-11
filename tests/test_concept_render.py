"""Fixed host acceptance of render supervision; real video checked separately."""

import importlib
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from tests.test_concept_scene import example
from app.render.aggregation import JobOutcome


class FakeRunner:
    def __init__(self, mode="success"):
        self.mode = mode
        self.calls = 0

    def run_job(self, job, command, cancel_event, on_log, env=None):
        self.calls += 1
        on_log("host fake runner - not real render")
        if self.mode == "timeout":
            cancel_event.wait(1)
            return SimpleNamespace(outcome=JobOutcome.CANCELLED, return_code=-15, diagnostic="")
        if self.mode == "fail":
            return SimpleNamespace(
                outcome=JobOutcome.FAILED, return_code=2, diagnostic="fixture failure"
            )
        if self.mode == "missing":
            return SimpleNamespace(
                outcome=JobOutcome.MANIM_NOT_FOUND, return_code=None, diagnostic="no manim"
            )
        if self.mode == "success":
            media = Path(command[command.index("--media_dir") + 1])
            video = media / "videos" / "scene" / "480p15" / "ConceptScene.mp4"
            video.parent.mkdir(parents=True)
            video.write_bytes(b"fake video, never evidence of a real render")
        return SimpleNamespace(outcome=JobOutcome.SUCCESS, return_code=0, diagnostic="")


class RenderContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.input = self.root / "input.json"
        self.input.write_text(json.dumps(example()), encoding="utf8")

    def render(self, runner=None, timeout=5):
        self.assertTrue(
            Path("concept_render/render_adapter.py").is_file(), "render adapter not implemented"
        )
        return importlib.import_module("concept_render.render_adapter").render(
            self.input, self.root / "out", timeout=timeout, runner=runner
        )

    def test_success_receipt_and_times(self):
        result = self.render(FakeRunner())
        self.assertEqual(result["status"], "success")
        self.assertTrue(Path(result["video"]).is_file())
        self.assertEqual(result["steps"][-1]["end"], 10)
        self.assertEqual(len(result["input_sha256"]), 64)
        self.assertEqual(result["requested_font"], "PingFang SC")

    def test_unique_output_directories(self):
        first = self.render(FakeRunner())
        second = self.render(FakeRunner())
        self.assertNotEqual(first["video"], second["video"])
        self.assertTrue(Path(first["video"]).is_file())

    def test_invalid_input_never_runs(self):
        self.input.write_text('{"version":99}', encoding="utf8")
        runner = FakeRunner()
        with self.assertRaises(ValueError):
            self.render(runner)
        self.assertEqual(runner.calls, 0)

    def test_failed_job_cannot_be_success(self):
        with self.assertRaises(RuntimeError):
            self.render(FakeRunner("fail"))
        receipts = list((self.root / "out").rglob("result.json"))
        self.assertTrue(receipts)
        self.assertEqual(json.loads(receipts[0].read_text())["status"], "failed")

    def test_missing_video_rejected(self):
        with self.assertRaises(RuntimeError):
            self.render(FakeRunner("no_video"))

    def test_timeout_is_cancellation(self):
        with self.assertRaises(RuntimeError):
            self.render(FakeRunner("timeout"), timeout=0.02)

    def test_missing_renderer_rejected(self):
        with self.assertRaises(RuntimeError):
            self.render(FakeRunner("missing"))

    def test_cli_and_probe_compile(self):
        for name in ["concept_render/__main__.py", "tools/verify_concept_video.py"]:
            self.assertTrue(Path(name).is_file(), name + " not implemented")
            compile(Path(name).read_text(), name, "exec")


class ConceptDependencyBoundary(unittest.TestCase):
    def test_new_vertical_uses_only_headless_render_core(self):
        import ast

        bad = []
        for file in Path("concept_render").glob("*.py"):
            for item in ast.walk(ast.parse(file.read_text())):
                modules = []
                if isinstance(item, ast.Import):
                    modules = [name.name for name in item.names]
                if isinstance(item, ast.ImportFrom) and item.module:
                    modules = [item.module]
                for module in modules:
                    if (
                        module.startswith(("explainer", "PyQt", "PySide"))
                        or module == "app.render.worker"
                        or (module.startswith("app.") and not module.startswith("app.render."))
                    ):
                        bad.append(str(file) + ": " + module)
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
