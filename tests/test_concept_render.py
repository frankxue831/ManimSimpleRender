"""Fixed host acceptance of render supervision; real video checked separately."""

import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
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
            return SimpleNamespace(
                render_cleanup_state="complete",
                outcome=JobOutcome.CANCELLED,
                return_code=-15,
                diagnostic="",
            )
        if self.mode == "fail":
            return SimpleNamespace(
                render_cleanup_state="complete",
                outcome=JobOutcome.FAILED,
                return_code=2,
                diagnostic="fixture failure",
            )
        if self.mode == "missing":
            return SimpleNamespace(
                render_cleanup_state="complete",
                outcome=JobOutcome.MANIM_NOT_FOUND,
                return_code=None,
                diagnostic="no manim",
            )
        if self.mode == "success":
            media = Path(command[command.index("--media_dir") + 1])
            video = media / "videos" / "scene" / "480p15" / "ConceptScene.mp4"
            video.parent.mkdir(parents=True)
            video.write_bytes(b"fake video, never evidence of a real render")
        return SimpleNamespace(
            render_cleanup_state="complete",
            outcome=JobOutcome.SUCCESS,
            return_code=0,
            diagnostic="",
        )


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

    def test_unknown_and_timer_persistence_failures_preserve_state(self):
        for state in (None, "bogus", "unknown", "not_started", "complete"):
            for fail_at in ("none", "join", "receipt", "runner_join"):
                with self.subTest(state=state, fail_at=fail_at):
                    runner = mock.Mock()
                    result = SimpleNamespace(
                        outcome=JobOutcome.SUCCESS, return_code=0, render_cleanup_state=state
                    )
                    runner.run_job.return_value = result
                    timer = mock.Mock(ident=1)
                    if "join" in fail_at:
                        timer.join.side_effect = RuntimeError("join failed")
                        timer.join.side_effect.render_cleanup_state = "complete"
                    if fail_at == "runner_join":
                        error = RuntimeError("runner failed")
                        error.render_cleanup_state = state
                        runner.run_job.side_effect = error
                    from concept_render import render_adapter as adapter

                    original = adapter._write_json
                    with (
                        mock.patch.object(adapter.threading, "Timer", return_value=timer),
                        mock.patch.object(
                            adapter,
                            "_write_json",
                            side_effect=(
                                OSError("disk failed") if fail_at == "receipt" else original
                            ),
                        ),
                        mock.patch("subprocess.Popen", side_effect=AssertionError("no child")),
                        mock.patch("os.kill", side_effect=AssertionError("no signal")),
                        mock.patch("os.killpg", side_effect=AssertionError("no signal")),
                    ):
                        with self.assertRaises(RuntimeError) as caught:
                            self.render(runner)
                    expected = (
                        state
                        if state == "complete"
                        or (state == "not_started" and fail_at == "runner_join")
                        else "unknown"
                    )
                    self.assertEqual(caught.exception.render_cleanup_state, expected)

    def test_failed_no_start_return_code_is_unknown_but_safe_controls_remain(self):
        from concept_render import render_adapter as adapter

        cases = (
            (JobOutcome.FAILED, 1, "not_started", "unknown"),
            (JobOutcome.SUCCESS, None, "not_started", "unknown"),
            (JobOutcome.SUCCESS, 0, "not_started", "unknown"),
            (JobOutcome.FAILED, None, "not_started", "not_started"),
            (JobOutcome.MANIM_NOT_FOUND, None, "not_started", "not_started"),
            (JobOutcome.FAILED, None, "complete", "complete"),
        )
        for outcome, code, state, expected in cases:
            with self.subTest(outcome=outcome, code=code, state=state):
                result = SimpleNamespace(
                    outcome=outcome, return_code=code, render_cleanup_state=state, diagnostic=""
                )
                with (
                    mock.patch.object(adapter.threading, "Timer", return_value=mock.Mock()),
                    mock.patch("subprocess.Popen", side_effect=AssertionError("no child")),
                    mock.patch("subprocess.run", side_effect=AssertionError("no command")),
                    mock.patch("os.kill", side_effect=AssertionError("no signal")),
                    mock.patch("os.killpg", side_effect=AssertionError("no signal"), create=True),
                ):
                    with self.assertRaises(RuntimeError) as caught:
                        self.render(mock.Mock(run_job=mock.Mock(return_value=result)))
                self.assertEqual(caught.exception.render_cleanup_state, expected)
                self.assertFalse(list((self.root / "out").rglob("*.mp4")))

    def test_all_failure_replacements_retain_state_and_interruptions(self):
        from concept_render import render_adapter as adapter

        for state in ("unknown", "complete", "not_started"):
            for kind in (OSError, ValueError, RuntimeError, KeyboardInterrupt, SystemExit):
                with self.subTest(state=state, kind=kind):
                    replacement = kind("receipt failure")
                    result = SimpleNamespace(
                        outcome=JobOutcome.FAILED,
                        return_code=None,
                        render_cleanup_state=state,
                        diagnostic="",
                    )
                    with (
                        mock.patch.object(adapter.threading, "Timer", return_value=mock.Mock()),
                        mock.patch.object(adapter, "_write_json", side_effect=replacement),
                        mock.patch("subprocess.Popen", side_effect=AssertionError("no child")),
                        mock.patch("subprocess.run", side_effect=AssertionError("no command")),
                        mock.patch("os.kill", side_effect=AssertionError("no signal")),
                        mock.patch(
                            "os.killpg", side_effect=AssertionError("no signal"), create=True
                        ),
                    ):
                        with self.assertRaises(BaseException) as caught:
                            self.render(mock.Mock(run_job=mock.Mock(return_value=result)))
                    expected = state if issubclass(kind, Exception) else "unknown"
                    self.assertEqual(caught.exception.render_cleanup_state, expected)
                    if not issubclass(kind, OSError):
                        self.assertIs(caught.exception, replacement)
                    if not issubclass(kind, Exception):
                        self.assertNotIsInstance(caught.exception, Exception)

    def test_formatting_error_retains_state_and_primary_interrupt_is_not_wrapped(self):
        from concept_render import render_adapter as adapter

        class BadMessage(RuntimeError):
            def __str__(self):
                raise ValueError("formatting failure")

        for error in (BadMessage(), KeyboardInterrupt(), SystemExit()):
            error.render_cleanup_state = "unknown"
            with (
                mock.patch.object(adapter.threading, "Timer", return_value=mock.Mock()),
                mock.patch.object(adapter, "_write_json", side_effect=OSError("disk")),
                mock.patch("subprocess.Popen", side_effect=AssertionError("no child")),
                mock.patch("subprocess.run", side_effect=AssertionError("no command")),
                mock.patch("os.kill", side_effect=AssertionError("no signal")),
                mock.patch("os.killpg", side_effect=AssertionError("no signal"), create=True),
            ):
                with self.assertRaises(BaseException) as caught:
                    self.render(mock.Mock(run_job=mock.Mock(side_effect=error)))
            self.assertEqual(caught.exception.render_cleanup_state, "unknown")
            if not isinstance(error, Exception):
                self.assertIs(caught.exception, error)

    def test_unknown_cleanup_diagnostic_survives_without_success_receipt(self):
        from concept_render import render_adapter as adapter

        for outcome in (JobOutcome.SUCCESS, JobOutcome.FAILED):
            with self.subTest(outcome=outcome):
                diagnostic = "owned group probe failed: fixture detail"

                class UncertainRunner(FakeRunner):
                    def run_job(self, *args, **kwargs):
                        result = super().run_job(*args, **kwargs)
                        result.render_cleanup_state = "unknown"
                        result.outcome = outcome
                        result.diagnostic = diagnostic
                        return result

                with (
                    mock.patch.object(adapter.threading, "Timer", return_value=mock.Mock()),
                    mock.patch("subprocess.Popen", side_effect=AssertionError("no child")),
                    mock.patch("subprocess.run", side_effect=AssertionError("no command")),
                    mock.patch("os.kill", side_effect=AssertionError("no signal")),
                    mock.patch("os.killpg", side_effect=AssertionError("no signal"), create=True),
                ):
                    with self.assertRaisesRegex(
                        RuntimeError, "render cleanup is unverified"
                    ) as caught:
                        self.render(UncertainRunner())
                self.assertEqual(caught.exception.render_cleanup_state, "unknown")
        receipts = list((self.root / "out").rglob("result.json"))
        self.assertEqual(len(receipts), 2)
        for path in receipts:
            result = json.loads(path.read_text())
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["stage"], "render")
            self.assertEqual(result["render_cleanup_state"], "unknown")
            self.assertNotIn("video", result)
            self.assertIn(diagnostic, Path(result["log"]).read_text())
            self.assertTrue(list(path.parent.rglob("ConceptScene.mp4")))

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
